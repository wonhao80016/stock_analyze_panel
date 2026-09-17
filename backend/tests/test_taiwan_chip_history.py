"""Unit tests for Taiwan institutional/margin chip history query service and routes.

Covers:
  - TaiwanChipHistoryService: read_range-backed history, ascending date order,
    total_net preference (computed_net over official_net), non-contiguous
    coverage surfaced as missing dates (not an error), unit passthrough (shares).
  - API routes: /institutional-history, /margin-history — empty array on no
    data, 404 on unknown symbol, 400 on malformed symbol format.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path

import polars as pl
import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.taiwan.chip_history import TaiwanChipHistoryService
from app.taiwan.institutional_store import TaiwanInstitutionalStore
from app.taiwan.margin_store import TaiwanMarginStore
from app.taiwan.realtime.calendar import TaiwanTradingCalendar

SYMBOL = "2330.TWSE"


@pytest.fixture
def inst_store(tmp_path: Path) -> TaiwanInstitutionalStore:
    return TaiwanInstitutionalStore(data_dir=tmp_path / "institutional")


@pytest.fixture
def margin_store(tmp_path: Path) -> TaiwanMarginStore:
    return TaiwanMarginStore(data_dir=tmp_path / "margin")


def _service(
    inst_store: TaiwanInstitutionalStore,
    margin_store: TaiwanMarginStore,
    symbol_exists=lambda s: True,
) -> TaiwanChipHistoryService:
    return TaiwanChipHistoryService(
        institutional_store=inst_store,
        margin_store=margin_store,
        calendar=TaiwanTradingCalendar(),
        symbol_exists=symbol_exists,
    )


class TestInstitutionalHistoryService:
    def test_returns_rows_sorted_ascending_by_date(self, inst_store, margin_store):
        df = pl.DataFrame([
            {"symbol": SYMBOL, "date": date(2026, 9, 15), "foreign_net": 100,
             "investment_trust_net": 10, "dealer_net": 5, "official_net": 115, "computed_net": 115},
            {"symbol": SYMBOL, "date": date(2026, 9, 10), "foreign_net": -50,
             "investment_trust_net": 20, "dealer_net": 0, "official_net": -30, "computed_net": -30},
            {"symbol": SYMBOL, "date": date(2026, 9, 12), "foreign_net": 30,
             "investment_trust_net": -10, "dealer_net": 2, "official_net": 22, "computed_net": 22},
        ])
        inst_store.write_batch(df)
        svc = _service(inst_store, margin_store)

        resp = svc.get_institutional_history(SYMBOL, days=60)

        assert [r.date for r in resp.rows] == ["2026-09-10", "2026-09-12", "2026-09-15"]
        assert resp.rows[0].foreign_net == -50
        assert resp.rows[-1].total_net == 115

    def test_total_net_prefers_computed_net_over_official_net(self, inst_store, margin_store):
        df = pl.DataFrame([
            {"symbol": SYMBOL, "date": date(2026, 9, 15), "foreign_net": 100,
             "investment_trust_net": 10, "dealer_net": 5, "official_net": 999, "computed_net": 115},
        ])
        inst_store.write_batch(df)
        svc = _service(inst_store, margin_store)

        resp = svc.get_institutional_history(SYMBOL, days=60)

        assert resp.rows[0].total_net == 115

    def test_falls_back_to_official_net_when_computed_net_missing(self, inst_store, margin_store):
        df = pl.DataFrame([
            {"symbol": SYMBOL, "date": date(2026, 9, 15), "foreign_net": 100,
             "investment_trust_net": 10, "dealer_net": 5, "official_net": 999, "computed_net": None},
        ])
        inst_store.write_batch(df)
        svc = _service(inst_store, margin_store)

        resp = svc.get_institutional_history(SYMBOL, days=60)

        assert resp.rows[0].total_net == 999

    def test_no_data_returns_empty_array_not_error(self, inst_store, margin_store):
        svc = _service(inst_store, margin_store)

        resp = svc.get_institutional_history(SYMBOL, days=60)

        assert resp.symbol == SYMBOL
        assert resp.rows == []

    def test_unknown_symbol_raises_lookup_error(self, inst_store, margin_store):
        svc = _service(inst_store, margin_store, symbol_exists=lambda s: False)

        with pytest.raises(LookupError):
            svc.get_institutional_history(SYMBOL, days=60)


class TestMarginHistoryService:
    def test_returns_shares_unconverted(self, inst_store, margin_store):
        # Store canonical unit is shares (股); provider layer already
        # converted 5 lots -> 5000 shares before writing (see enrichment/margin.py).
        df = pl.DataFrame([
            {"symbol": SYMBOL, "date": date(2026, 9, 16), "margin_balance": 5000,
             "margin_change": 1000, "short_balance": 2000, "short_change": -500,
             "short_margin_ratio": 40.0},
        ])
        margin_store.write_batch(df)
        svc = _service(inst_store, margin_store)

        resp = svc.get_margin_history(SYMBOL, days=60)

        assert resp.rows[0].margin_balance == 5000
        assert resp.rows[0].margin_change == 1000
        assert resp.rows[0].short_balance == 2000
        assert resp.rows[0].short_change == -500
        assert resp.rows[0].short_margin_ratio == 40.0

    def test_non_contiguous_coverage_is_missing_dates_not_error(self, inst_store, margin_store):
        df = pl.DataFrame([
            {"symbol": SYMBOL, "date": date(2026, 4, 7), "margin_balance": 1000},
            {"symbol": SYMBOL, "date": date(2026, 9, 16), "margin_balance": 2000},
        ])
        margin_store.write_batch(df)
        svc = _service(inst_store, margin_store)

        resp = svc.get_margin_history(SYMBOL, days=120)

        assert [r.date for r in resp.rows] == ["2026-04-07", "2026-09-16"]

    def test_no_data_returns_empty_array_not_error(self, inst_store, margin_store):
        svc = _service(inst_store, margin_store)

        resp = svc.get_margin_history(SYMBOL, days=60)

        assert resp.symbol == SYMBOL
        assert resp.rows == []

    def test_unknown_symbol_raises_lookup_error(self, inst_store, margin_store):
        svc = _service(inst_store, margin_store, symbol_exists=lambda s: False)

        with pytest.raises(LookupError):
            svc.get_margin_history(SYMBOL, days=60)


class TestHistoryRoutes:
    def test_institutional_history_route_returns_rows(self, inst_store, margin_store, monkeypatch):
        df = pl.DataFrame([
            {"symbol": SYMBOL, "date": date(2026, 9, 16), "foreign_net": 100,
             "investment_trust_net": 10, "dealer_net": 5, "official_net": 115, "computed_net": 115},
        ])
        inst_store.write_batch(df)
        svc = _service(inst_store, margin_store)
        monkeypatch.setattr("app.api.taiwan.get_taiwan_chip_history_service", lambda: svc)
        client = TestClient(app, client=("127.0.0.1", 50000))

        resp = client.get(f"/api/taiwan/stocks/{SYMBOL}/institutional-history?days=60")

        assert resp.status_code == 200
        body = resp.json()
        assert body["symbol"] == SYMBOL
        assert len(body["rows"]) == 1
        assert body["rows"][0]["total_net"] == 115

    def test_margin_history_route_no_data_returns_empty_array_not_404(self, inst_store, margin_store, monkeypatch):
        svc = _service(inst_store, margin_store)
        monkeypatch.setattr("app.api.taiwan.get_taiwan_chip_history_service", lambda: svc)
        client = TestClient(app, client=("127.0.0.1", 50000))

        resp = client.get(f"/api/taiwan/stocks/{SYMBOL}/margin-history?days=60")

        assert resp.status_code == 200
        assert resp.json()["rows"] == []

    def test_history_route_unknown_symbol_returns_404(self, inst_store, margin_store, monkeypatch):
        svc = _service(inst_store, margin_store, symbol_exists=lambda s: False)
        monkeypatch.setattr("app.api.taiwan.get_taiwan_chip_history_service", lambda: svc)
        client = TestClient(app, client=("127.0.0.1", 50000))

        resp = client.get(f"/api/taiwan/stocks/{SYMBOL}/institutional-history")

        assert resp.status_code == 404

        resp2 = client.get(f"/api/taiwan/stocks/{SYMBOL}/margin-history")

        assert resp2.status_code == 404

    def test_history_route_invalid_symbol_format_returns_400(self):
        client = TestClient(app, client=("127.0.0.1", 50000))

        assert client.get("/api/taiwan/stocks/not-a-symbol/institutional-history").status_code == 400
        assert client.get("/api/taiwan/stocks/not-a-symbol/margin-history").status_code == 400
