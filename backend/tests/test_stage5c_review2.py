"""Exchange checks read only the symbol column, latest partition first; ROC Chinese date stamps are parsed."""
from __future__ import annotations

from datetime import date, datetime

import polars as pl
import pytest

from app.taiwan import detail_service
from app.taiwan.detail_service import TaiwanStockDetailService
from app.taiwan.enrichment.institutional import stamped_for
from app.taiwan.enrichment.margin import TwseMarginAdapter
from app.taiwan.institutional_store import TaiwanInstitutionalStore
from app.taiwan.margin_store import TaiwanMarginStore
from app.taiwan.symbol import parse_symbol

SESSIONS = [date(2026, 10, d) for d in (15, 16, 19, 20, 21, 22)]
PREV, DAY = SESSIONS[-2], SESSIONS[-1]


def _flows(symbols: list[str], value: int) -> pl.DataFrame:
    n = len(symbols)
    return pl.DataFrame({"symbol": symbols, "foreign_net": [value] * n, "investment_trust_net": [0] * n,
                         "dealer_net": [0] * n, "computed_net": [value] * n, "official_net": [value] * n})


class _Spy:
    """Real store that records every full read_range call."""

    def __init__(self, store) -> None:
        self.store = store
        self.full_reads: list[tuple] = []

    def __getattr__(self, name):
        return getattr(self.store, name)

    def read_range(self, symbols, start, end):
        if not symbols:
            self.full_reads.append((start, end))
        return self.store.read_range(symbols, start, end)


@pytest.fixture
def svc(monkeypatch):
    monkeypatch.setattr(detail_service, "taipei_now", lambda: datetime(2026, 10, 22, 22, 0))
    return TaiwanStockDetailService()


@pytest.fixture
def inst_store(tmp_path, monkeypatch):
    store = TaiwanInstitutionalStore(data_dir=tmp_path / "institutional")
    for i, d in enumerate(SESSIONS[:-1]):
        store.write_batch(_flows(["1410.TWSE", "8069.TPEX"], 100 * (i + 1)), partition_date=d)
    store.write_batch(_flows(["8069.TPEX"], 1), partition_date=DAY)
    spy = _Spy(store)
    monkeypatch.setattr("app.taiwan.institutional_store.TaiwanInstitutionalStore", lambda *a, **k: spy)
    return spy


def test_institutional_fallback_skips_the_incomplete_day_without_full_reads(svc, inst_store):
    out = svc._institutional_from_store(parse_symbol("1410.TWSE"))

    assert out is not None
    assert out.meta.trade_date == PREV.isoformat()
    assert out.foreign_net == 500
    assert inst_store.full_reads == []


def test_five_day_sum_is_unknown_over_an_incomplete_day_without_full_reads(svc, inst_store):
    assert svc._institutional_5d("1410.TWSE", DAY) == dict.fromkeys(("foreign_net", "investment_trust_net", "dealer_net"))
    assert svc._institutional_5d("1410.TWSE", PREV) == {"foreign_net": 100 + 200 + 300 + 400 + 500,
                                                         "investment_trust_net": 0, "dealer_net": 0}
    assert inst_store.full_reads == []


def test_margin_fallback_skips_the_incomplete_day_without_full_reads(svc, tmp_path, monkeypatch):
    store = TaiwanMarginStore(data_dir=tmp_path / "margin")
    store.write_batch(pl.DataFrame({"symbol": ["1410.TWSE", "8069.TPEX"], "margin_balance": [9000, 1],
                                    "short_balance": [90, 0]}), partition_date=PREV)
    store.write_batch(pl.DataFrame({"symbol": ["8069.TPEX"], "margin_balance": [1], "short_balance": [0]}),
                      partition_date=DAY)
    spy = _Spy(store)
    monkeypatch.setattr("app.taiwan.margin_store.TaiwanMarginStore", lambda *a, **k: spy)

    out = svc._margin_from_store(parse_symbol("1410.TWSE"))

    assert out is not None
    assert (out.status, out.margin_balance, out.short_margin_ratio) == ("available", 9000, 1.0)
    assert out.meta.trade_date == PREV.isoformat()
    assert spy.full_reads == []


@pytest.mark.parametrize(
    ("stamps", "expected"),
    [
        (["115年10月22日"], True),
        (["115年10月21日"], False),
        (["20261022", "115年10月22日"], True),
        (["garbage"], False),
        (["garbage", "115/10/22"], True),
        ([None, ""], True),
        ([], True),
    ],
)
def test_stamped_for(stamps, expected):
    assert stamped_for(stamps, DAY) is expected


def test_twse_margin_table_stamped_in_roc_chinese_for_another_date_is_rejected():
    row = ["2330", "name", "1", "2", "0", "10", "11", "", "0", "1", "0", "3", "4", "", "", ""]
    payload = {"stat": "OK", "tables": [{"date": "115年10月21日", "data": [row]}]}

    assert TwseMarginAdapter().parse_payload(payload, DAY, "u") == []
