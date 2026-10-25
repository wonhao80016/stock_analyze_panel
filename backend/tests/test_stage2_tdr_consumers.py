"""Consumers of the security master treat Taiwan depositary receipts (TDRs) as their own class.

A TDR has no domestic industry, its official share count is the underlying company's shares
(「已發行普通股數或TDR原股發行股數」), its par value may be in a foreign currency, and its
transfer is taxed at 0.1% rather than the 0.3% of an ordinary stock (MOF etax Q&A LeJ1WWV).
"""

from __future__ import annotations

from datetime import date
from types import SimpleNamespace
from unittest.mock import MagicMock

import httpx
import polars as pl
import pytest

from app.backtest.engine import MatcherConfig
from app.backtest.strategy import _configure_taiwan_matcher
from app.taiwan.abnormal_diagnostics import TaiwanAbnormalDiagnosticsService
from app.taiwan.ai_research import build_evidence_registry
from app.taiwan.fundamentals import TaiwanOfficialFundamentals
from app.taiwan.industry_intelligence import TaiwanIndustryIntelligenceService
from app.taiwan.market_rules import SecuritiesTaxModel, TaxClass
from app.taiwan.research_context import TaiwanStockResearchContextService
from app.taiwan.screener import TaiwanScreenerRequest, TaiwanScreenerService
from app.taiwan.share_capital_store import TaiwanShareCapitalStore
from app.taiwan.universe.models import MarketProfileBridge, TaiwanInstrument
from app.taiwan.universe.service import UniverseType

pytestmark = pytest.mark.usefixtures("deny_network")


def _instrument(
    symbol: str,
    name: str,
    industry: str | None,
    *,
    instrument_type: str = "stock",
    security_class: str | None = None,
) -> TaiwanInstrument:
    code, exchange = symbol.split(".")
    return TaiwanInstrument(
        symbol=symbol,
        code=code,
        exchange=exchange,
        name=name,
        instrument_type=instrument_type,
        listing_status="active",
        listing_date="2010-01-04",
        isin=None,
        industry=industry,
        cfi_code=None,
        raw_category="股票" if instrument_type == "stock" else "ETF",
        is_supported=True,
        source="TWSE_OPENAPI",
        updated_at="2026-10-07T16:00:00+08:00",
        etf_category="domestic_equity" if instrument_type == "etf" else None,
        classification_source="official_metadata" if instrument_type == "etf" else None,
        underlying_scope="domestic" if instrument_type == "etf" else None,
        security_class=security_class,
    )


TSMC = _instrument("2330.TWSE", "台積電", "半導體業")
UMC = _instrument("2303.TWSE", "聯電", "半導體業")
TDR = _instrument("910861.TWSE", "神州-DR", None, security_class="tdr")
TINGYI = _instrument("910322.TWSE", "康師傅-DR", None, security_class="tdr")
NO_INDUSTRY = _instrument("1234.TWSE", "無產業股", None)
ETF = _instrument("0050.TWSE", "元大台灣50", None, instrument_type="etf")


class _Master:
    def __init__(self, *instruments: TaiwanInstrument) -> None:
        self._by_symbol = {inst.symbol: inst for inst in instruments}

    def ensure_loaded(self) -> None:
        return None

    def get_instrument(self, symbol: str) -> TaiwanInstrument | None:
        return self._by_symbol.get(symbol)

    def get_universe(self, universe_type) -> list[str]:
        wanted = {
            UniverseType.TAIWAN_STOCKS: {"stock"},
            UniverseType.TAIWAN_ETFS: {"etf"},
        }.get(universe_type, {"stock", "etf"})
        return [s for s, inst in self._by_symbol.items() if inst.instrument_type in wanted]

    def search(self, q: str, limit: int = 20) -> list[dict]:
        return []

    def to_dataframe(self, supported_only: bool = False) -> pl.DataFrame:
        return pl.DataFrame([inst.to_dict() for inst in self._by_symbol.values()])


# ── Industry intelligence and its filters ─────────────────────


def test_industry_intelligence_counts_a_tdr_as_unclassified():
    d = date(2026, 10, 6)
    daily = MagicMock()
    daily.available_dates.return_value = [date(2026, 10, 5), d]
    daily.read_range.return_value = pl.DataFrame()

    snap = TaiwanIndustryIntelligenceService(
        daily_store=daily, security_master=_Master(TSMC, UMC, TDR)
    ).get_snapshot(d)

    assert snap.data_quality.unclassified_stock_count == 1
    names = {ind.industry for ind in snap.industries}
    assert "UNCLASSIFIED" in names
    assert not any("91" in name for name in names)


def test_screener_unclassified_industry_filter_returns_the_tdr():
    svc = TaiwanScreenerService(security_master=_Master(TSMC, TDR, ETF), daily_store=MagicMock())
    df = pl.DataFrame(
        {
            "symbol": ["2330.TWSE", "910861.TWSE", "0050.TWSE"],
            "instrument_type": ["stock", "stock", "etf"],
            "industry": ["半導體業", None, None],
        }
    )

    got = svc._apply_filters(df, TaiwanScreenerRequest(industry="UNCLASSIFIED"))

    assert got["symbol"].to_list() == ["910861.TWSE"]
    named = svc._apply_filters(df, TaiwanScreenerRequest(industry="半導體業"))
    assert named["symbol"].to_list() == ["2330.TWSE"]


def _tdr_daily(d_curr: date, p_dates: list[date]) -> MagicMock:
    """910861 closed 4.04 five sessions ago and 4.53 on the target date; 2330 moved 0%."""
    p5_closes = [4.04, 4.20, 4.30, 4.40, 4.45]
    daily = MagicMock()
    daily.available_dates.return_value = [*p_dates, d_curr]

    def read_range(symbols, start, end):
        if start == d_curr:
            return pl.DataFrame(
                {
                    "symbol": ["910861.TWSE", "2330.TWSE", "1234.TWSE"],
                    "date": [d_curr] * 3,
                    "close": [4.53, 1000.0, 50.0],
                    "volume": [1000.0] * 3,
                    "amount": [10000.0] * 3,
                }
            )
        rows = []
        for day, tdr_close in zip(p_dates, p5_closes, strict=True):
            if start <= day <= end:
                rows.append(
                    {
                        "symbol": "910861.TWSE",
                        "date": day,
                        "close": tdr_close,
                        "volume": 1000.0,
                        "amount": 10000.0,
                    }
                )
                rows.append(
                    {
                        "symbol": "2330.TWSE",
                        "date": day,
                        "close": 1000.0,
                        "volume": 1000.0,
                        "amount": 10000.0,
                    }
                )
                rows.append(
                    {
                        "symbol": "1234.TWSE",
                        "date": day,
                        "close": 40.0,
                        "volume": 1000.0,
                        "amount": 10000.0,
                    }
                )
        return pl.DataFrame(rows) if rows else pl.DataFrame()

    daily.read_range.side_effect = read_range
    return daily


def _industry_svc() -> MagicMock:
    def metric(name: str) -> MagicMock:
        m = MagicMock()
        m.industry = name
        m.average_return_5d = 0.0
        m.average_return_5d_excluding.return_value = 0.0
        m.relative_strength_5d = 0.0
        m.relative_strength_20d = 0.0
        m.turnover_share = 0.1
        m.advance_ratio = 0.5
        return m

    svc = MagicMock()
    svc.get_snapshot.return_value.industries = [metric("半導體業"), metric("UNCLASSIFIED")]
    return svc


def test_abnormal_relative_strength_is_not_applicable_to_a_tdr_or_unknown_industry():
    d_curr = date(2026, 10, 6)
    p_dates = [
        date(2026, 9, 29),
        date(2026, 9, 30),
        date(2026, 10, 1),
        date(2026, 10, 2),
        date(2026, 10, 5),
    ]
    svc = TaiwanAbnormalDiagnosticsService(
        daily_store=_tdr_daily(d_curr, p_dates),
        security_master=_Master(TSMC, TDR, NO_INDUSTRY),
        industry_intel_svc=_industry_svc(),
    )

    snap = svc.get_diagnostics(target_date=d_curr, include_all=True)
    items = {item.symbol: item for item in snap.items}

    tdr = items["910861.TWSE"]
    assert all(s.type != "RELATIVE_STRENGTH_OUTLIER" for s in tdr.signals)
    assert tdr.not_applicable_signals == ["RELATIVE_STRENGTH_OUTLIER"]
    assert items["1234.TWSE"].not_applicable_signals == ["RELATIVE_STRENGTH_OUTLIER"]
    assert all(s.type != "RELATIVE_STRENGTH_OUTLIER" for s in items["1234.TWSE"].signals)
    assert items["2330.TWSE"].not_applicable_signals == []


def test_abnormal_unclassified_industry_filter_returns_the_tdr():
    d_curr = date(2026, 10, 6)
    p_dates = [
        date(2026, 9, 29),
        date(2026, 9, 30),
        date(2026, 10, 1),
        date(2026, 10, 2),
        date(2026, 10, 5),
    ]
    svc = TaiwanAbnormalDiagnosticsService(
        daily_store=_tdr_daily(d_curr, p_dates),
        security_master=_Master(TSMC, TDR, ETF),
        industry_intel_svc=_industry_svc(),
    )

    snap = svc.get_diagnostics(
        target_date=d_curr, include_all=True, industry_filter="UNCLASSIFIED", include_etfs=True
    )

    assert [item.symbol for item in snap.items] == ["910861.TWSE"]


# ── Research context and AI evidence ──────────────────────────


def test_research_context_marks_tdr_industry_not_applicable(tmp_path):
    master = _Master(TSMC, TDR)
    ind_svc = MagicMock()
    svc = TaiwanStockResearchContextService(security_master=master, industry_intel_svc=ind_svc)

    ctx = svc.get_research_context("910861.TWSE", date(2026, 10, 6))

    assert ctx.identity.security_class == "tdr"
    assert ctx.industry_context.status == "not_applicable"
    assert "industry" not in ctx.evidence_summary.missing_sections
    ind_svc.get_snapshot.assert_not_called()

    payload, keys, missing = build_evidence_registry(ctx, None)
    assert "industry" not in missing and "industry_context" not in missing
    assert "industry_not_applicable_for_tdr" in missing
    assert payload["identity"]["security_class"] == "tdr"
    assert "identity.security_class" in keys


def test_research_ai_evidence_uses_a_provisional_industry_snapshot():
    metric = SimpleNamespace(
        industry="半導體業",
        turnover=1.0e9,
        turnover_share=0.3,
        advance_ratio=0.6,
        average_change_pct=0.01,
        median_change_pct=0.01,
        foreign_net=0.0,
        investment_trust_net=0.0,
        relative_strength_5d=0.02,
        relative_strength_20d=0.03,
    )
    ind_svc = MagicMock()
    ind_svc.get_snapshot.return_value = SimpleNamespace(
        industries=[metric],
        trade_date="2026-10-06",
        data_quality=SimpleNamespace(overall_status="provisional"),
    )
    svc = TaiwanStockResearchContextService(
        security_master=_Master(TSMC, TDR), industry_intel_svc=ind_svc
    )

    ctx = svc.get_research_context("2330.TWSE", date(2026, 10, 6))
    payload, keys, missing = build_evidence_registry(ctx, None)

    assert ctx.industry_context.status == "provisional"
    assert payload["industry_context"]["status"] == "provisional"
    assert payload["industry_context"]["relative_strength_5d"] == 0.02
    assert {"industry_context.status", "industry_context.relative_strength_5d"} <= keys
    assert "industry_context" not in missing


def test_ai_prompts_state_tdr_industry_is_not_applicable():
    from app.taiwan.ai_research import SYSTEM_PROMPT
    from app.taiwan.comparison_ai_research import SYSTEM_PROMPT_COMPARISON
    from app.taiwan.screener_nl import SYSTEM_PROMPT as SYSTEM_PROMPT_NL

    assert "industry_not_applicable_for_tdr" in SYSTEM_PROMPT
    assert "{symbol}.industry_not_applicable_for_tdr" in SYSTEM_PROMPT_COMPARISON
    assert "UNCLASSIFIED" in SYSTEM_PROMPT_NL
    assert "industry_context.status 為 provisional" in SYSTEM_PROMPT
    assert "{symbol}.industry_context.status 為 provisional" in SYSTEM_PROMPT_COMPARISON


# ── Share capital, turnover and market cap ────────────────────


def _old_format_capital_file(path) -> None:
    """A share_capital.parquet as written before S5: no security_class column."""
    pl.DataFrame(
        {
            "symbol": ["2330.TWSE", "910322.TWSE", "9103.TWSE"],
            "issued_shares": [25930380458, 5620600000, 100000000],
            "report_date": ["2026-09-21"] * 3,
            "retrieved_at": ["2026-09-21T18:00:00+08:00"] * 3,
        }
    ).write_parquet(path / "share_capital.parquet")


class _CountingMaster(_Master):
    def __init__(self, cache_path, *instruments: TaiwanInstrument) -> None:
        super().__init__(*instruments)
        self.cache_path = cache_path
        self.scans = 0

    def to_dataframe(self, supported_only: bool = False) -> pl.DataFrame:
        self.scans += 1
        return super().to_dataframe(supported_only)


def _master_with_tdrs(tmp_path, monkeypatch) -> _CountingMaster:
    import app.taiwan.share_capital_store as capital_module
    import app.taiwan.universe as universe_module

    cache_path = tmp_path / "security_master.parquet"
    cache_path.write_bytes(b"v1")
    medical = _instrument("9103.TWSE", "美德醫療-DR", None, security_class="tdr")
    master = _CountingMaster(cache_path, TSMC, TINGYI, medical)
    monkeypatch.setattr(universe_module, "get_security_master", lambda: master)
    monkeypatch.setattr(capital_module, "_master_tdrs", None)
    return master


def test_share_capital_as_instruments_excludes_master_tdrs_from_an_old_file(tmp_path, monkeypatch):
    _master_with_tdrs(tmp_path, monkeypatch)
    _old_format_capital_file(tmp_path)
    store = TaiwanShareCapitalStore(data_dir=tmp_path)

    assert store.as_instruments()["symbol"].to_list() == ["2330.TWSE"]
    assert set(store.load()["symbol"]) == {"2330.TWSE", "910322.TWSE", "9103.TWSE"}


def test_share_capital_scans_the_master_once_per_master_file_version(tmp_path, monkeypatch):
    master = _master_with_tdrs(tmp_path, monkeypatch)
    _old_format_capital_file(tmp_path)
    store = TaiwanShareCapitalStore(data_dir=tmp_path)

    store.as_instruments()
    store.as_instruments()
    assert master.scans == 1

    master.cache_path.write_bytes(b"version 2")
    assert store.as_instruments()["symbol"].to_list() == ["2330.TWSE"]
    assert master.scans == 2


def test_share_capital_falls_back_to_tdr_codes_without_a_master(tmp_path, monkeypatch):
    import app.taiwan.universe as universe_module

    def unavailable():
        raise OSError("security master unavailable")

    monkeypatch.setattr(universe_module, "get_security_master", unavailable)
    _old_format_capital_file(tmp_path)

    got = TaiwanShareCapitalStore(data_dir=tmp_path).as_instruments()

    assert got["symbol"].to_list() == ["2330.TWSE", "9103.TWSE"]


class _CapitalStore:
    def load(self) -> pl.DataFrame:
        return pl.DataFrame(
            {
                "symbol": ["2330.TWSE", "910322.TWSE"],
                "issued_shares": [25930380458, 5620600000],
                "report_date": ["2026-09-21"] * 2,
                "retrieved_at": ["2026-09-21T18:00:00+08:00"] * 2,
            }
        )


def test_kline_info_has_no_share_count_for_a_tdr(monkeypatch):
    import app.taiwan.share_capital_store as capital_module
    import app.taiwan.universe as universe_module
    from app.api.kline import _get_taiwan_stock_info

    master = _Master(TSMC, TINGYI)
    monkeypatch.setattr(universe_module, "get_security_master", lambda: master)
    monkeypatch.setattr(capital_module, "TaiwanShareCapitalStore", _CapitalStore)

    assert _get_taiwan_stock_info("2330.TWSE")["total_shares"] == 25930380458
    assert _get_taiwan_stock_info("910322.TWSE")["total_shares"] is None


def test_watchlist_turnover_is_null_for_a_tdr():
    from app.taiwan.realtime.calendar import taipei_now
    from app.taiwan.watchlist_enrichment import enrich_taiwan_watchlist_rows

    today = taipei_now().date()

    def quote(price: float) -> SimpleNamespace:
        return SimpleNamespace(
            last_price=price,
            open=price,
            high=price,
            low=price,
            prev_close=price,
            change=0.0,
            change_pct=0.0,
            amount=1.0,
            volume=1_000_000,
            trade_date=today,
            quote_time=None,
            source_meta=None,
        )

    realtime = MagicMock()
    realtime.get_quotes.return_value = {"2330.TWSE": quote(1000.0), "910322.TWSE": quote(23.0)}
    daily = MagicMock()
    daily.latest_date.return_value = None

    rows = enrich_taiwan_watchlist_rows(
        ["2330.TWSE", "910322.TWSE"],
        security_master=_Master(TSMC, TINGYI),
        realtime_service=realtime,
        daily_store=daily,
        share_capital=_CapitalStore(),
    )
    by_symbol = {row["symbol"]: row for row in rows}

    assert by_symbol["2330.TWSE"]["turnover_rate"] == pytest.approx(1_000_000 * 100.0 / 25930380458)
    assert by_symbol["910322.TWSE"]["turnover_rate"] is None


# ── Fundamentals par value ────────────────────────────────────


@pytest.mark.parametrize(
    ("raw", "twd", "currency"),
    [
        ("新台幣 10.0000元", 10.0, "TWD"),
        ("美金0.0050元", None, "USD"),
        ("港幣0.1元", None, "HKD"),
        ("HKD 0.1", None, "HKD"),
        ("泰銖7.5元", None, "THB"),
        ("0.1", None, None),
        ("無面額", None, None),
    ],
)
def test_share_capital_par_value_keeps_currency_and_raw_text(raw, twd, currency):
    row = {
        "出表日期": "1150922",
        "公司代號": "910322",
        "普通股每股面額": raw,
        "實收資本額": "916513457",
        "已發行普通股數或TDR原股發行股數": "5620600000",
    }
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=[row]))

    got = TaiwanOfficialFundamentals(httpx.Client(transport=transport)).share_capital(
        "910322.TWSE", "TWSE"
    )

    assert got.values["par_value_twd"] == twd
    assert got.values["par_value_raw"] == raw
    assert got.values["par_value_currency"] == currency


@pytest.mark.parametrize("raw", ["", "-", "--", "N/A"])
def test_share_capital_missing_par_value_has_no_raw_text(raw):
    row = {
        "出表日期": "1150922",
        "公司代號": "6969",
        "普通股每股面額": raw,
        "實收資本額": "100000000",
        "已發行普通股數或TDR原股發行股數": "10000000",
    }
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=[row]))

    got = TaiwanOfficialFundamentals(httpx.Client(transport=transport)).share_capital(
        "6969.TWSE", "TWSE"
    )

    assert got.values["par_value_raw"] is None
    assert got.values["par_value_twd"] is None
    assert got.values["par_value_currency"] is None


# ── Securities transaction tax ────────────────────────────────


def test_tdr_tax_class_and_rate():
    assert MarketProfileBridge.get_tax_class(TDR) == TaxClass.TDR
    assert MarketProfileBridge.get_tax_class(TSMC) == TaxClass.ORDINARY_STOCK
    model = SecuritiesTaxModel()
    assert model.get_tax_rate(TaxClass.TDR, False, date(2026, 10, 6)) == 0.001
    assert model.get_tax_rate(TaxClass.TDR, True, date(2026, 10, 6)) == 0.001


def test_backtest_execution_charges_tdr_sell_tax_at_one_tenth_percent():
    tdr = _instrument("910322.TWSE", "康師傅-DR", None, security_class="tdr")
    stock = _instrument("2330.TWSE", "台積電", "半導體業")
    config = MatcherConfig(fees_pct=0.0002, slippage_bps=0)

    _configure_taiwan_matcher(
        config, ("910322.TWSE", "2330.TWSE"), security_master=_Master(tdr, stock)
    )

    # Commission 0.1425% plus sell tax: 0.1% for a TDR, 0.3% for an ordinary stock.
    assert config.sell_transaction_cost(100_000, "910322.TWSE", date(2026, 10, 6)) == pytest.approx(
        242.5
    )
    assert config.sell_transaction_cost(100_000, "2330.TWSE", date(2026, 10, 6)) == pytest.approx(
        442.5
    )
    config.day_trade_eligible_by_symbol = {"910322.TWSE": True}
    assert config.sell_transaction_cost(
        100_000, "910322.TWSE", date(2026, 10, 6), is_day_trade=True
    ) == pytest.approx(242.5)


# ── Stock detail identity ─────────────────────────────────────


def test_detail_identity_exposes_security_class():
    from app.taiwan.detail_service import TaiwanStockDetailService

    svc = TaiwanStockDetailService(
        hybrid_provider=MagicMock(**{"get_daily.return_value": pl.DataFrame()}),
        institutional_provider=MagicMock(**{"fetch_live_day.side_effect": RuntimeError("offline")}),
        margin_provider=MagicMock(**{"fetch_live_day.side_effect": RuntimeError("offline")}),
        daily_store=MagicMock(**{"scan_range.return_value": pl.DataFrame()}),
    )
    svc.security_master = _Master(TSMC, TINGYI)
    svc.realtime_service = MagicMock(**{"get_quotes.return_value": {}})

    assert svc.get_stock_detail("910322.TWSE").identity.security_class == "tdr"
    assert svc.get_stock_detail("2330.TWSE").identity.security_class is None
