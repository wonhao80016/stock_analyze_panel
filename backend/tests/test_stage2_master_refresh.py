"""The Taiwan security master refreshes from the official directories without ever losing the old file.

A refresh validates the new list before replacing data/taiwan/security_master.parquet atomically,
keeps vanished symbols as delisted, stamps one Asia/Taipei timestamp and records the outcome in
security_master.refresh.json. Other processes see a replaced file on their next query.
"""

from __future__ import annotations

import json
import threading
import time
from dataclasses import replace
from datetime import date, datetime
from unittest.mock import MagicMock

import httpx
import polars as pl
import pytest

from app.indicators import pipeline
from app.taiwan.market_intelligence import TaiwanMarketIntelligenceService
from app.taiwan.realtime.calendar import TAIPEI_TZ
from app.taiwan.universe import TaiwanSecurityMaster, UniverseType, adapters
from app.taiwan.universe import service as service_module
from app.taiwan.universe.adapters import TpexInstrumentAdapter
from app.taiwan.universe.models import TaiwanInstrument

pytestmark = pytest.mark.usefixtures("deny_network")

NOW = datetime(2026, 10, 7, 16, 30, tzinfo=TAIPEI_TZ)
OLD_STAMP = "2026-09-17T08:00:00+08:00"
FETCHED_STAMP = "2026-10-07T16:29:58"
TWSE_STOCKS = [f"{1101 + i}.TWSE" for i in range(10)]
TPEX_STOCKS = ["8069.TPEX", "6488.TPEX"]


def _stock(
    symbol: str, updated_at: str = FETCHED_STAMP, listing_date: str = "2001-07-23"
) -> TaiwanInstrument:
    code, exchange = symbol.split(".")
    return TaiwanInstrument(
        symbol=symbol,
        code=code,
        exchange=exchange,
        name=f"name-{code}",
        instrument_type="stock",
        listing_status="active",
        listing_date=listing_date,
        isin=None,
        industry="半導體業",
        cfi_code=None,
        raw_category="股票",
        is_supported=True,
        source=f"{exchange}_OPENAPI",
        updated_at=updated_at,
        underlying_scope="domestic",
    )


def _etf(
    symbol: str,
    *,
    category: str = "bond",
    scope: str = "foreign",
    source: str = "cfi_code",
    style: str | None = None,
    updated_at: str = FETCHED_STAMP,
) -> TaiwanInstrument:
    code, exchange = symbol.split(".")
    return TaiwanInstrument(
        symbol=symbol,
        code=code,
        exchange=exchange,
        name=f"etf-{code}",
        instrument_type="etf",
        listing_status="active",
        listing_date="2018-02-01",
        isin=None,
        industry=None,
        cfi_code="CEOIBU" if category == "bond" else None,
        raw_category="ETF",
        is_supported=True,
        source=f"{exchange}_ISIN",
        updated_at=updated_at,
        etf_category=category,
        classification_source=source,
        underlying_scope=scope,
        etf_management_style=style,
    )


def _baseline() -> dict[str, TaiwanInstrument]:
    items = [_stock(s, OLD_STAMP) for s in TWSE_STOCKS + TPEX_STOCKS]
    items += [
        _etf(
            "0050.TWSE",
            category="domestic_equity",
            scope="domestic",
            source="official_metadata",
            style="passive",
            updated_at=OLD_STAMP,
        ),
        _etf("00720B.TPEX", style="passive", updated_at=OLD_STAMP),
        _etf("00679B.TPEX", style=None, updated_at=OLD_STAMP),
    ]
    return {item.symbol: item for item in items}


def _fresh_twse(
    extra: list[TaiwanInstrument] | None = None, drop: set[str] | None = None
) -> list[TaiwanInstrument]:
    drop = drop or set()
    items = [_stock(s) for s in TWSE_STOCKS if s not in drop]
    items.append(
        _etf(
            "0050.TWSE",
            category="domestic_equity",
            scope="domestic",
            source="official_metadata",
            style="passive",
        )
    )
    return items + (extra or [])


def _fresh_tpex() -> list[TaiwanInstrument]:
    return [_stock(s) for s in TPEX_STOCKS] + [_etf("00720B.TPEX"), _etf("00679B.TPEX")]


class _Adapter:
    def __init__(
        self, instruments: list[TaiwanInstrument] | None = None, error: Exception | None = None
    ):
        self.instruments = instruments or []
        self.error = error
        self.calls = 0

    def get_instruments(self, html_content=None):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return list(self.instruments)


def _detail(code: str, category: str) -> dict:
    return {
        "tables": [
            {
                "title": "商品規格",
                "fields": ["名稱", "ETF簡稱", "證券代號", "ETF類別", "上櫃日期"],
                "data": [["fund", "etf", code, category, "2018/02/01"]],
            }
        ],
        "stat": "ok",
    }


class _DetailServer:
    def __init__(self, styles: dict[str, str] | None = None, fail: bool = False):
        self.styles = styles or {}
        self.fail = fail
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.fail:
            raise httpx.ConnectError("boom", request=request)
        code = request.url.params["code"]
        if code not in self.styles:
            return httpx.Response(200, json={"stat": "暫無相關商品"})
        return httpx.Response(200, json=_detail(code, self.styles[code]))

    @property
    def codes(self) -> list[str]:
        return [r.url.params["code"] for r in self.requests]


class _TpexAdapter(TpexInstrumentAdapter):
    def __init__(
        self, instruments=None, server: _DetailServer | None = None, error: Exception | None = None
    ):
        self.server = server or _DetailServer()
        super().__init__(transport=httpx.MockTransport(self.server), detail_request_interval=0.0)
        self.instruments = instruments if instruments is not None else _fresh_tpex()
        self.error = error
        self.calls = 0

    def get_instruments(self, html_content=None, official_product_types=None):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return list(self.instruments)


@pytest.fixture(autouse=True)
def _no_retry_sleep(monkeypatch):
    monkeypatch.setattr(service_module, "_REPLACE_RETRY_SECONDS", 0.0, raising=False)


def _seeded_master(tmp_path, twse=None, tpex=None) -> TaiwanSecurityMaster:
    path = tmp_path / "security_master.parquet"
    seed = TaiwanSecurityMaster(cache_path=path, twse_adapter=_Adapter(), tpex_adapter=_Adapter())
    seed._replace_instruments(_baseline())
    seed.save_cache()
    master = TaiwanSecurityMaster(
        cache_path=path,
        twse_adapter=twse or _Adapter(_fresh_twse()),
        tpex_adapter=tpex or _TpexAdapter(),
    )
    assert master.load_cache() is True
    return master


def _metadata(master: TaiwanSecurityMaster) -> dict:
    return json.loads(
        (master.cache_path.parent / "security_master.refresh.json").read_text(encoding="utf-8")
    )


def test_refresh_installs_a_new_listing_in_process_with_one_taipei_stamp(tmp_path):
    master = _seeded_master(
        tmp_path, twse=_Adapter(_fresh_twse([_stock("7812.TWSE", listing_date="2026-10-07")]))
    )
    pipeline._taiwan_limit_profile_cache["7812.TWSE"] = (None, False, None)

    result = master.refresh(now=NOW)

    assert result.ok is True, result.error
    assert result.added == ["7812.TWSE"]
    assert master.get_instrument("7812.TWSE") is not None
    assert "7812.TWSE" not in pipeline._taiwan_limit_profile_cache
    stored = pl.read_parquet(master.cache_path)
    assert "7812.TWSE" in stored["symbol"].to_list()
    assert stored["updated_at"].unique().to_list() == [NOW.isoformat()]
    meta = _metadata(master)
    assert meta["last_success"] == NOW.isoformat()
    assert meta["last_attempt"] == NOW.isoformat()
    assert meta["error"] is None
    assert meta["added"] == ["7812.TWSE"]
    assert meta["removed"] == []
    assert meta["counts"]["TWSE"] == {"stock": 11, "etf": 1}
    assert meta["counts"]["TPEX"] == {"stock": 2, "etf": 2}


@pytest.mark.parametrize(
    ("twse", "tpex", "message"),
    [
        pytest.param(
            lambda: _Adapter(_fresh_twse()),
            lambda: _TpexAdapter(instruments=[]),
            "TPEX",
            id="tpex-empty",
        ),
        pytest.param(
            lambda: _Adapter(error=httpx.ConnectError("offline")),
            lambda: _TpexAdapter(),
            "offline",
            id="adapter-raises",
        ),
        pytest.param(
            lambda: _Adapter(_fresh_twse(drop={"1101.TWSE", "1102.TWSE"})),
            lambda: _TpexAdapter(),
            "stock",
            id="count-drop",
        ),
    ],
)
def test_invalid_refresh_keeps_old_file_and_memory(tmp_path, twse, tpex, message):
    master = _seeded_master(tmp_path, twse=twse(), tpex=tpex())
    before_bytes = master.cache_path.read_bytes()
    before = dict(master._instruments)

    result = master.refresh(now=NOW)

    assert result.ok is False
    assert message in result.error
    assert master.cache_path.read_bytes() == before_bytes
    assert master._instruments == before
    meta = _metadata(master)
    assert meta["last_attempt"] == NOW.isoformat()
    assert meta["last_success"] is None
    assert message in meta["error"]


def test_failed_refresh_keeps_the_previous_success_in_metadata(tmp_path):
    master = _seeded_master(tmp_path)
    assert master.refresh(now=NOW).ok
    master.tpex_adapter = _TpexAdapter(instruments=[])

    later = datetime(2026, 10, 8, 16, 30, tzinfo=TAIPEI_TZ)
    assert master.refresh(now=later).ok is False

    meta = _metadata(master)
    assert meta["last_success"] == NOW.isoformat()
    assert meta["last_attempt"] == later.isoformat()
    assert "TPEX" in meta["error"]


def test_symbol_missing_from_new_list_is_kept_as_delisted(tmp_path):
    master = _seeded_master(tmp_path, twse=_Adapter(_fresh_twse(drop={"1105.TWSE"})))

    result = master.refresh(now=NOW)

    assert result.ok is True, result.error
    assert result.removed == ["1105.TWSE"]
    inst = master.get_instrument("1105.TWSE")
    assert inst is not None and inst.listing_status == "delisted"
    assert "1105.TWSE" not in master.get_universe(UniverseType.TAIWAN_ALL)
    assert "1105.TWSE" not in master.get_universe(UniverseType.TAIWAN_STOCKS)
    assert "1105.TWSE" not in master.to_provider_dataframe("stock")["symbol"].to_list()
    reloaded = TaiwanSecurityMaster(cache_path=master.cache_path)
    assert reloaded.load_cache() is True
    assert reloaded.get_instrument("1105.TWSE").listing_status == "delisted"


def test_write_failure_during_refresh_keeps_old_file(tmp_path, monkeypatch):
    master = _seeded_master(tmp_path, twse=_Adapter(_fresh_twse([_stock("7812.TWSE")])))
    before_bytes = master.cache_path.read_bytes()
    before = dict(master._instruments)

    def broken_write(self, file, *args, **kwargs):
        with open(file, "wb") as handle:
            handle.write(b"partial")
        raise OSError("disk full")

    monkeypatch.setattr(pl.DataFrame, "write_parquet", broken_write)
    result = master.refresh(now=NOW)

    assert result.ok is False
    assert "disk full" in result.error
    assert master.cache_path.read_bytes() == before_bytes
    assert master._instruments == before
    assert not [p.name for p in tmp_path.iterdir() if p.name.endswith(".tmp")]


def test_save_cache_failure_keeps_old_file(tmp_path, monkeypatch):
    master = _seeded_master(tmp_path)
    before_bytes = master.cache_path.read_bytes()

    def broken_write(self, file, *args, **kwargs):
        with open(file, "wb") as handle:
            handle.write(b"partial")
        raise OSError("disk full")

    monkeypatch.setattr(pl.DataFrame, "write_parquet", broken_write)
    with pytest.raises(OSError, match="disk full"):
        master.save_cache()

    assert master.cache_path.read_bytes() == before_bytes
    assert [p.name for p in tmp_path.iterdir()] == ["security_master.parquet"]


def test_file_replaced_by_another_process_is_seen_by_the_next_query(tmp_path):
    reader = _seeded_master(tmp_path)
    assert reader.get_instrument("7856.TPEX") is None

    writer = TaiwanSecurityMaster(cache_path=reader.cache_path)
    assert writer.load_cache() is True
    writer._replace_instruments({**writer._instruments, "7856.TPEX": _stock("7856.TPEX")})
    writer.save_cache()

    assert reader.get_instrument("7856.TPEX") is not None


def test_corrupt_cache_never_triggers_a_live_fetch_or_overwrite(tmp_path):
    path = tmp_path / "security_master.parquet"
    path.write_bytes(b"not a parquet file")
    twse, tpex = _Adapter(_fresh_twse()), _TpexAdapter()
    master = TaiwanSecurityMaster(cache_path=path, twse_adapter=twse, tpex_adapter=tpex)

    with pytest.raises(service_module.SecurityMasterUnavailableError):
        master.get_instrument("1101.TWSE")

    assert twse.calls == 0 and tpex.calls == 0
    assert path.read_bytes() == b"not a parquet file"


def test_cache_corrupted_after_load_keeps_current_data(tmp_path):
    twse, tpex = _Adapter(_fresh_twse()), _TpexAdapter()
    master = _seeded_master(tmp_path, twse=twse, tpex=tpex)
    master.cache_path.write_bytes(b"truncated by a crashed writer")

    assert master.get_instrument("1101.TWSE") is not None
    assert twse.calls == 0 and tpex.calls == 0
    assert master.cache_path.read_bytes() == b"truncated by a crashed writer"


def test_replace_blocked_once_by_a_reader_is_retried(tmp_path, monkeypatch):
    master = _seeded_master(tmp_path, twse=_Adapter(_fresh_twse([_stock("7812.TWSE")])))
    real_replace = service_module.os.replace
    calls: list[str] = []

    def flaky_replace(src, dst):
        calls.append(str(dst))
        if len(calls) == 1:
            raise PermissionError(5, "Access is denied", str(dst))
        return real_replace(src, dst)

    monkeypatch.setattr(service_module.os, "replace", flaky_replace)
    result = master.refresh(now=NOW)

    assert result.ok is True, result.error
    assert calls[0] == calls[1] == str(master.cache_path)
    assert "7812.TWSE" in pl.read_parquet(master.cache_path)["symbol"].to_list()


def _market_service(
    master: TaiwanSecurityMaster, target: date, symbols: list[str] | None = None
) -> TaiwanMarketIntelligenceService:
    symbols = symbols if symbols is not None else master.get_universe(UniverseType.TAIWAN_ALL)

    def rows(d: date) -> pl.DataFrame:
        return pl.DataFrame(
            {
                "symbol": symbols,
                "date": [d] * len(symbols),
                "open": [100.0] * len(symbols),
                "high": [101.0] * len(symbols),
                "low": [99.0] * len(symbols),
                "close": [100.0] * len(symbols),
                "volume": [1000.0] * len(symbols),
                "amount": [100000.0] * len(symbols),
                "quote_ts": [None] * len(symbols),
            }
        )

    previous = date(2026, 9, 1)
    daily = MagicMock()
    daily.available_dates.return_value = [previous, target]
    daily.provisional_dates.return_value = []
    daily.read_range.side_effect = lambda _symbols, start, end: (
        rows(start) if start == end else pl.concat([rows(previous), rows(target)])
    )
    inst_store = MagicMock()
    inst_store.available_dates.return_value = [target]
    inst_store.read_range.return_value = pl.DataFrame()
    margin_store = MagicMock()
    margin_store.available_dates.return_value = [target]
    margin_store.read_range.return_value = pl.DataFrame()
    market_inst = MagicMock()
    market_inst.available_dates.return_value = []
    market_inst.read_day.return_value = pl.DataFrame(
        schema={
            "exchange": pl.String,
            "category": pl.String,
            "net_amount": pl.Int64,
            "has_discrepancy": pl.Boolean,
        }
    )
    corporate_actions = MagicMock()
    corporate_actions.read.return_value = pl.DataFrame()
    return TaiwanMarketIntelligenceService(
        daily_store=daily,
        inst_store=inst_store,
        margin_store=margin_store,
        security_master=master,
        market_inst_store=market_inst,
        corporate_actions=corporate_actions,
    )


def test_market_intelligence_is_complete_when_master_refreshed_on_target(tmp_path):
    master = _seeded_master(tmp_path, twse=_Adapter(_fresh_twse(drop={"1105.TWSE"})))
    assert master.refresh(now=NOW).ok

    target = NOW.date()
    snapshot = _market_service(master, target).get_snapshot(target)

    assert snapshot.data_quality.overall_status == "complete"
    assert snapshot.data_quality.missing_symbols_count == 0


def test_dry_run_reports_the_diff_without_writing(tmp_path):
    reclassified = _etf(
        "0050.TWSE",
        category="foreign_equity",
        scope="foreign",
        source="official_metadata",
        style="passive",
    )
    twse = [inst for inst in _fresh_twse(drop={"1105.TWSE"}) if inst.symbol != "0050.TWSE"]
    twse += [reclassified, _stock("7812.TWSE")]
    server = _DetailServer({"00679B": "被動式國外債券及固定收益ETF"})
    master = _seeded_master(tmp_path, twse=_Adapter(twse), tpex=_TpexAdapter(server=server))
    before_bytes = master.cache_path.read_bytes()
    before = dict(master._instruments)

    result = master.refresh(now=NOW, dry_run=True)

    assert result.ok is True and result.dry_run is True
    assert result.added == ["7812.TWSE"]
    assert result.removed == ["1105.TWSE"]
    assert result.changed["0050.TWSE"]["etf_category"] == ["domestic_equity", "foreign_equity"]
    assert result.changed["0050.TWSE"]["price_limit_pct"] == [0.1, None]
    assert result.changed["00679B.TPEX"]["etf_management_style"] == [None, "passive"]
    assert master.cache_path.read_bytes() == before_bytes
    assert master._instruments == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["security_master.parquet"]


def test_tpex_management_style_is_looked_up_only_for_unknown_codes(tmp_path):
    server = _DetailServer(
        {
            "00679B": "被動式國外債券及固定收益ETF",
            "00980D": "主動式國外債券ETF",
            "00720B": "主動式國外債券ETF",
        }
    )
    tpex = _TpexAdapter(instruments=[*_fresh_tpex(), _etf("00980D.TPEX")], server=server)
    master = _seeded_master(tmp_path, tpex=tpex)

    result = master.refresh(now=NOW)

    assert result.ok is True, result.error
    assert sorted(server.codes) == ["00679B", "00980D"]
    assert all(
        r.url.params["response"] == "json" and r.url.params["type"] == "bond"
        for r in server.requests
    )
    assert master.get_instrument("00720B.TPEX").etf_management_style == "passive"
    assert master.get_instrument("00679B.TPEX").etf_management_style == "passive"
    assert master.get_instrument("00980D.TPEX").etf_management_style == "active"
    assert _metadata(master)["management_style_lookups"] == {"queried": 2, "resolved": 2}


def test_failed_style_lookups_leave_style_unknown_and_stop_early(tmp_path):
    server = _DetailServer(fail=True)
    extra = [_etf(f"0099{i}B.TPEX") for i in range(5)]
    master = _seeded_master(
        tmp_path, tpex=_TpexAdapter(instruments=_fresh_tpex() + extra, server=server)
    )

    result = master.refresh(now=NOW)

    assert result.ok is True, result.error
    assert len(server.requests) == 3
    assert master.get_instrument("00679B.TPEX").etf_management_style is None
    assert master.get_instrument("00720B.TPEX").etf_management_style == "passive"


def test_detail_payload_for_another_code_is_ignored():
    assert (
        adapters.parse_tpex_etf_management_style(
            _detail("00679B", "被動式國外債券及固定收益ETF"), "00679B"
        )
        == "passive"
    )
    assert (
        adapters.parse_tpex_etf_management_style(
            _detail("00687B (加掛美元ETF)", "被動式國外債券"), "00687B"
        )
        == "passive"
    )
    assert (
        adapters.parse_tpex_etf_management_style(_detail("00687C", "被動式國外債券"), "00687B")
        is None
    )
    assert adapters.parse_tpex_etf_management_style({"stat": "暫無相關商品"}, "00687B") is None
    assert (
        adapters.parse_tpex_etf_management_style(_detail("00687B", "國外債券ETF"), "00687B") is None
    )


def test_unchanged_instruments_are_not_reported_as_reclassified(tmp_path):
    master = _seeded_master(tmp_path)

    result = master.refresh(now=NOW, dry_run=True)

    assert result.ok is True
    assert result.changed == {}
    assert result.added == [] and result.removed == []


def test_previously_delisted_symbol_stays_delisted_and_is_not_removed_again(tmp_path):
    master = _seeded_master(tmp_path, twse=_Adapter(_fresh_twse(drop={"1105.TWSE"})))
    master._replace_instruments(
        {
            **master._instruments,
            "1105.TWSE": replace(master._instruments["1105.TWSE"], listing_status="delisted"),
        }
    )
    master.save_cache()

    result = master.refresh(now=NOW)

    assert result.ok is True
    assert result.removed == []
    assert master.get_instrument("1105.TWSE").listing_status == "delisted"


def test_detail_lookups_are_paced(monkeypatch):
    sleeps: list[float] = []
    monkeypatch.setattr(adapters.time, "sleep", sleeps.append)
    server = _DetailServer({"00679B": "被動式國外債券", "00720B": "被動式國外債券"})
    adapter = TpexInstrumentAdapter(
        transport=httpx.MockTransport(server), detail_request_interval=0.5
    )

    styles = dict(adapter.iter_etf_management_styles(
        {"00679B": "bond", "00720B": "bond", "00980D": "bond"}
    ))

    assert styles == {"00679B": "passive", "00720B": "passive", "00980D": None}
    assert sleeps == [0.5, 0.5]


def _master_from(tmp_path, baseline, twse, tpex=None) -> TaiwanSecurityMaster:
    path = tmp_path / "security_master.parquet"
    seed = TaiwanSecurityMaster(cache_path=path, twse_adapter=_Adapter(), tpex_adapter=_Adapter())
    seed._replace_instruments(baseline)
    seed.save_cache()
    master = TaiwanSecurityMaster(
        cache_path=path, twse_adapter=twse, tpex_adapter=tpex or _TpexAdapter()
    )
    assert master.load_cache() is True
    return master


def test_historical_target_after_refresh_is_complete_with_full_coverage(tmp_path):
    master = _seeded_master(tmp_path)
    assert master.refresh(now=NOW).ok

    target = date(2026, 9, 30)
    snapshot = _market_service(master, target).get_snapshot(target)

    assert snapshot.data_quality.overall_status == "complete"
    assert snapshot.data_quality.missing_symbols_count == 0


def test_symbol_delisted_after_target_counts_for_that_target_only(tmp_path):
    master = _seeded_master(tmp_path, twse=_Adapter(_fresh_twse(drop={"1105.TWSE"})))
    assert master.refresh(now=NOW).ok
    stored = pl.read_parquet(master.cache_path)
    assert stored.filter(pl.col("symbol") == "1105.TWSE")["delisted_at"].to_list() == ["2026-10-07"]
    everything = sorted(_baseline())

    day_before = date(2026, 10, 6)
    before = _market_service(master, day_before, everything).get_snapshot(day_before)
    on_refresh_day = _market_service(master, NOW.date()).get_snapshot(NOW.date())

    assert before.data_quality.universe_supported_symbols == 15
    assert before.data_quality.overall_status == "complete"
    assert on_refresh_day.data_quality.universe_supported_symbols == 14
    assert on_refresh_day.data_quality.overall_status == "complete"


def test_delisted_at_is_kept_while_the_symbol_stays_delisted(tmp_path):
    master = _seeded_master(tmp_path, twse=_Adapter(_fresh_twse(drop={"1105.TWSE"})))
    assert master.refresh(now=NOW).ok

    assert master.refresh(now=datetime(2026, 10, 8, 16, 30, tzinfo=TAIPEI_TZ)).ok

    stored = pl.read_parquet(master.cache_path)
    assert stored.filter(pl.col("symbol") == "1105.TWSE")["delisted_at"].to_list() == ["2026-10-07"]


def test_delisted_symbol_reappearing_becomes_active_again(tmp_path):
    master = _seeded_master(tmp_path, twse=_Adapter(_fresh_twse(drop={"1105.TWSE"})))
    assert master.refresh(now=NOW).ok
    master.twse_adapter = _Adapter(_fresh_twse())

    result = master.refresh(now=datetime(2026, 10, 8, 16, 30, tzinfo=TAIPEI_TZ))

    assert result.ok is True, result.error
    assert result.added == ["1105.TWSE"]
    assert master.get_instrument("1105.TWSE").listing_status == "active"
    assert "1105.TWSE" in master.get_universe(UniverseType.TAIWAN_STOCKS)
    stored = pl.read_parquet(master.cache_path)
    assert stored.filter(pl.col("symbol") == "1105.TWSE")["delisted_at"].to_list() == [None]


def test_malformed_detail_payload_only_loses_that_code(tmp_path):
    class _Server(_DetailServer):
        def __call__(self, request):
            if request.url.params["code"] == "00679B":
                self.requests.append(request)
                payload = _detail("00679B", "x")
                payload["tables"][0]["data"] = [["fund", "etf", "00679B"]]
                return httpx.Response(200, json=payload)
            return super().__call__(request)

    server = _Server({"00980D": "主動式國外債券ETF"})
    tpex = _TpexAdapter(instruments=[*_fresh_tpex(), _etf("00980D.TPEX")], server=server)
    master = _seeded_master(tmp_path, tpex=tpex)

    result = master.refresh(now=NOW)

    assert result.ok is True, result.error
    assert master.get_instrument("00679B.TPEX").etf_management_style is None
    assert master.get_instrument("00980D.TPEX").etf_management_style == "active"


def test_lookup_failing_mid_batch_keeps_the_styles_already_resolved(tmp_path):
    class _Breaking(_TpexAdapter):
        def iter_etf_management_styles(self, detail_types, **kwargs):
            yield "00679B", "passive"
            raise RuntimeError("adapter bug")

    tpex = _Breaking(instruments=[*_fresh_tpex(), _etf("00980D.TPEX")])
    master = _seeded_master(tmp_path, tpex=tpex)

    result = master.refresh(now=NOW)

    assert result.ok is True, result.error
    assert master.get_instrument("00679B.TPEX").etf_management_style == "passive"
    assert master.get_instrument("00980D.TPEX").etf_management_style is None


def test_externally_replaced_master_drops_stale_limit_profiles(tmp_path, monkeypatch):
    reader = _seeded_master(tmp_path)
    monkeypatch.setattr("app.taiwan.universe.get_security_master", lambda: reader)
    assert pipeline.taiwan_limit_profile(["0050.TWSE"])["limit_pct"][0] == pytest.approx(0.10)

    stored = pl.read_parquet(reader.cache_path)
    is_0050 = pl.col("symbol") == "0050.TWSE"
    replaced = stored.with_columns(
        pl.when(is_0050)
        .then(pl.lit("foreign_equity"))
        .otherwise(pl.col("etf_category"))
        .alias("etf_category"),
        pl.when(is_0050)
        .then(pl.lit("foreign"))
        .otherwise(pl.col("underlying_scope"))
        .alias("underlying_scope"),
    )
    staged = tmp_path / "written_by_another_process.parquet"
    replaced.write_parquet(staged)
    staged.replace(reader.cache_path)

    assert pipeline.taiwan_limit_profile(["0050.TWSE"])["limit_pct"][0] is None


def _large_baseline(extra: int) -> dict[str, TaiwanInstrument]:
    baseline = _baseline()
    baseline.update(
        {f"{2000 + i}.TWSE": _stock(f"{2000 + i}.TWSE", OLD_STAMP) for i in range(extra)}
    )
    return baseline


def _large_twse(extra: int, dropped: int) -> list[TaiwanInstrument]:
    kept = [_stock(f"{2000 + i}.TWSE") for i in range(dropped, extra)]
    newcomers = [_stock(f"{3000 + i}.TWSE") for i in range(dropped)]
    return _fresh_twse() + kept + newcomers


def test_removing_more_than_the_cap_needs_force(tmp_path):
    master = _master_from(tmp_path, _large_baseline(40), _Adapter(_large_twse(40, 21)))
    before_bytes = master.cache_path.read_bytes()

    rejected = master.refresh(now=NOW)

    assert rejected.ok is False
    assert "TWSE" in rejected.error and "21" in rejected.error and "force" in rejected.error
    assert master.cache_path.read_bytes() == before_bytes

    accepted = master.refresh(now=NOW, force=True)

    assert accepted.ok is True, accepted.error
    assert len(accepted.removed) == 21


def test_removals_within_the_cap_pass(tmp_path):
    master = _master_from(tmp_path, _large_baseline(40), _Adapter(_large_twse(40, 20)))

    result = master.refresh(now=NOW)

    assert result.ok is True, result.error
    assert len(result.removed) == 20


def test_force_also_accepts_a_count_drop(tmp_path):
    master = _seeded_master(tmp_path, twse=_Adapter(_fresh_twse(drop={"1101.TWSE", "1102.TWSE"})))

    assert master.refresh(now=NOW).ok is False
    assert master.refresh(now=NOW, force=True).ok is True


def test_force_does_not_accept_an_empty_exchange(tmp_path):
    master = _seeded_master(tmp_path, tpex=_TpexAdapter(instruments=[]))

    result = master.refresh(now=NOW, force=True)

    assert result.ok is False
    assert "TPEX" in result.error


def test_unreadable_cache_falls_back_to_the_loaded_master_as_baseline(tmp_path):
    server = _DetailServer()
    master = _seeded_master(
        tmp_path, twse=_Adapter(_fresh_twse(drop={"1105.TWSE"})), tpex=_TpexAdapter(server=server)
    )
    master.cache_path.write_bytes(b"garbage")

    result = master.refresh(now=NOW)

    assert result.ok is True, result.error
    assert result.removed == ["1105.TWSE"]
    assert "00720B" not in server.codes
    assert master.get_instrument("00720B.TPEX").etf_management_style == "passive"


def test_first_load_without_cache_goes_through_refresh(tmp_path):
    server = _DetailServer({"00679B": "被動式國外債券"})
    path = tmp_path / "security_master.parquet"
    master = TaiwanSecurityMaster(
        cache_path=path,
        twse_adapter=_Adapter(_fresh_twse([_stock("7812.TWSE")])),
        tpex_adapter=_TpexAdapter(server=server),
    )

    assert master.get_instrument("7812.TWSE") is not None

    stored = pl.read_parquet(path)
    assert stored["updated_at"].n_unique() == 1
    assert stored["updated_at"][0].endswith("+08:00")
    metadata = json.loads(master.refresh_metadata_path.read_text(encoding="utf-8"))
    assert metadata["last_success"]
    assert server.requests == []


def test_first_load_failure_raises_unavailable_without_writing(tmp_path):
    path = tmp_path / "security_master.parquet"
    master = TaiwanSecurityMaster(
        cache_path=path,
        twse_adapter=_Adapter(error=httpx.ConnectError("offline")),
        tpex_adapter=_TpexAdapter(),
    )

    with pytest.raises(service_module.SecurityMasterUnavailableError, match="offline"):
        master.get_instrument("1101.TWSE")

    assert not path.exists()


def test_refresh_held_by_another_process_returns_busy_without_fetching(tmp_path):
    twse = _Adapter(_fresh_twse())
    master = _seeded_master(tmp_path, twse=twse)
    before_bytes = master.cache_path.read_bytes()
    lock_path = master.cache_path.with_suffix(".refresh.lock")

    with service_module._refresh_file_lock(lock_path) as held:
        assert held is True
        result = master.refresh(now=NOW)

    assert result.ok is False and result.busy is True
    assert twse.calls == 0
    assert master.cache_path.read_bytes() == before_bytes
    assert master.refresh(now=NOW).ok is True


def test_refresh_running_in_this_process_returns_busy(tmp_path):
    twse = _Adapter(_fresh_twse())
    master = _seeded_master(tmp_path, twse=twse)

    with master._refresh_lock:
        result = master.refresh(now=NOW)

    assert result.busy is True
    assert twse.calls == 0


def test_metadata_write_failure_does_not_fail_the_refresh(tmp_path):
    master = _seeded_master(tmp_path, twse=_Adapter(_fresh_twse([_stock("7812.TWSE")])))
    (tmp_path / "security_master.refresh.json").mkdir()

    result = master.refresh(now=NOW)

    assert result.ok is True, result.error
    assert "7812.TWSE" in pl.read_parquet(master.cache_path)["symbol"].to_list()
    assert master.get_instrument("7812.TWSE") is not None


def test_unavailable_master_maps_to_http_503(monkeypatch):
    from fastapi.testclient import TestClient

    from app.main import app

    class _Unavailable:
        def search(self, q, limit=20):
            raise service_module.SecurityMasterUnavailableError(
                "Taiwan security master cache security_master.parquet is unreadable"
            )

    monkeypatch.setattr("app.taiwan.universe.get_security_master", lambda: _Unavailable())

    response = TestClient(app).get("/api/kline/instruments/search", params={"q": "2330"})

    assert response.status_code == 503
    assert "security master" in response.json()["detail"]


def test_first_load_with_an_empty_exchange_writes_nothing(tmp_path):
    path = tmp_path / "security_master.parquet"
    master = TaiwanSecurityMaster(
        cache_path=path,
        twse_adapter=_Adapter(_fresh_twse()),
        tpex_adapter=_TpexAdapter(instruments=[]),
    )

    with pytest.raises(service_module.SecurityMasterUnavailableError, match="TPEX"):
        master.get_instrument("1101.TWSE")

    assert not path.exists()


def test_unusable_lock_file_fails_the_refresh_without_raising(tmp_path):
    master = _seeded_master(tmp_path)
    before_bytes = master.cache_path.read_bytes()
    master.refresh_lock_path.mkdir()

    result = master.refresh(now=NOW)

    assert result.ok is False
    assert "lock" in result.error
    assert master.cache_path.read_bytes() == before_bytes


def test_unusable_lock_file_on_first_load_is_unavailable(tmp_path):
    path = tmp_path / "security_master.parquet"
    master = TaiwanSecurityMaster(
        cache_path=path, twse_adapter=_Adapter(_fresh_twse()), tpex_adapter=_TpexAdapter()
    )
    master.refresh_lock_path.mkdir()

    with pytest.raises(service_module.SecurityMasterUnavailableError, match="lock"):
        master.get_instrument("1101.TWSE")


def test_first_load_waits_for_a_refresh_running_elsewhere(tmp_path, monkeypatch):
    monkeypatch.setattr(service_module, "_FIRST_LOAD_POLL_SECONDS", 0.05, raising=False)
    path = tmp_path / "security_master.parquet"
    twse = _Adapter(error=AssertionError("must not download"))
    master = TaiwanSecurityMaster(cache_path=path, twse_adapter=twse, tpex_adapter=_TpexAdapter())
    holding = threading.Event()

    def other_process_refresh():
        with service_module._refresh_file_lock(master.refresh_lock_path) as held:
            assert held
            holding.set()
            time.sleep(0.3)
            writer = TaiwanSecurityMaster(cache_path=path)
            writer._replace_instruments(_baseline())
            writer.save_cache()

    worker = threading.Thread(target=other_process_refresh)
    worker.start()
    assert holding.wait(5)

    assert master.get_instrument("1101.TWSE") is not None

    worker.join(5)
    assert twse.calls == 0


def test_first_load_gives_up_when_the_other_refresh_never_finishes(tmp_path, monkeypatch):
    monkeypatch.setattr(service_module, "_FIRST_LOAD_POLL_SECONDS", 0.01, raising=False)
    monkeypatch.setattr(service_module, "_FIRST_LOAD_WAIT_SECONDS", 0.1, raising=False)
    path = tmp_path / "security_master.parquet"
    twse = _Adapter(_fresh_twse())
    master = TaiwanSecurityMaster(cache_path=path, twse_adapter=twse, tpex_adapter=_TpexAdapter())

    with service_module._refresh_file_lock(master.refresh_lock_path) as held:
        assert held
        with pytest.raises(service_module.SecurityMasterUnavailableError, match="in progress"):
            master.get_instrument("1101.TWSE")

    assert twse.calls == 0
    assert not path.exists()


def test_market_intelligence_endpoint_maps_an_unavailable_master_to_503(monkeypatch):
    from fastapi.testclient import TestClient

    from app.api import taiwan as taiwan_api
    from app.main import app

    class _Service:
        def get_snapshot(self, target_date=None):
            raise service_module.SecurityMasterUnavailableError(
                "Taiwan security master is unavailable"
            )

    monkeypatch.setattr(taiwan_api, "TaiwanMarketIntelligenceService", lambda: _Service())

    response = TestClient(app).get("/api/taiwan/market-intelligence")

    assert response.status_code == 503
    assert "security master" in response.json()["detail"]
