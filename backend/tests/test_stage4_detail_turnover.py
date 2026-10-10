"""The stock detail K-line carries the published turnover; the repository reads any float width of it."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from unittest.mock import MagicMock

import polars as pl
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api.taiwan import router as taiwan_router
from app.repository import DataStore, KlineRepository
from app.taiwan import detail_service
from app.taiwan.corporate_actions import TaiwanCorporateActionStore
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.detail_models import TaiwanDailyRow
from app.taiwan.detail_service import TaiwanStockDetailService

pytestmark = pytest.mark.usefixtures("deny_network")

STOCK = "2330.TWSE"
ETF = "0050.TWSE"
DAYS = [date(2026, 9, 21), date(2026, 9, 22), date(2026, 9, 23), date(2026, 9, 24)]
UNPUBLISHED = DAYS[2]
TURNOVER = {DAYS[0]: 0.5, DAYS[1]: 1.25, DAYS[3]: 2.0}
STORED_DTYPE = {DAYS[0]: pl.Int64, DAYS[1]: pl.Float32, DAYS[3]: pl.Float64}


def _bar(symbol: str, day: date, close: float) -> dict:
    return {
        "symbol": symbol,
        "date": day,
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": 1_000_000.0,
        "amount": close * 1_000_000.0,
    }


def _publish(data_dir: Path) -> None:
    """One enriched partition per published day; the stored turnover width varies by day."""
    for day, value in TURNOVER.items():
        stored = value if STORED_DTYPE[day] != pl.Int64 else round(value)
        frame = pl.DataFrame(
            [
                {**_bar(STOCK, day, 1000.0), "raw_close": 1000.0, "turnover_rate": stored},
                {**_bar(ETF, day, 190.0), "raw_close": 190.0, "turnover_rate": None},
            ],
            schema_overrides={"turnover_rate": STORED_DTYPE[day]},
        )
        path = data_dir / "kline_daily_enriched" / f"date={day.isoformat()}" / "part.parquet"
        path.parent.mkdir(parents=True, exist_ok=True)
        frame.write_parquet(path)


def _expected(day: date) -> float | None:
    if day not in TURNOVER:
        return None
    return float(round(TURNOVER[day])) if STORED_DTYPE[day] == pl.Int64 else TURNOVER[day]


@pytest.fixture
def repo(tmp_path) -> KlineRepository:
    _publish(tmp_path)
    return KlineRepository(DataStore(tmp_path))


def _service(tmp_path: Path, repository: KlineRepository | None) -> TaiwanStockDetailService:
    daily = TaiwanDailyStore(data_dir=tmp_path / "daily")
    daily.write_batch(
        pl.DataFrame(
            [
                {**_bar(symbol, day, close), "quote_ts": None}
                for symbol, close in ((STOCK, 1000.0), (ETF, 190.0))
                for day in DAYS
            ],
            schema_overrides={"quote_ts": pl.Int64},
        )
    )
    return TaiwanStockDetailService(
        daily_store=daily,
        hybrid_provider=MagicMock(),
        corporate_actions=TaiwanCorporateActionStore(data_dir=tmp_path / "corporate_actions"),
        repository=repository,
    )


def test_published_daily_rows_keep_turnover_stored_as_float32_or_int(repo):
    rows = repo._published_daily_symbol(STOCK, DAYS[0], DAYS[-1])

    assert dict(rows.select("date", "turnover_rate").iter_rows()) == {
        day: _expected(day) for day in TURNOVER
    }
    assert rows.schema["turnover_rate"] == pl.Float64


def test_daily_turnover_column_read_keeps_turnover_stored_as_float32_or_int(repo):
    rows = repo.get_daily(STOCK, DAYS[0], DAYS[-1], columns=["date", "turnover_rate"])

    assert dict(rows.iter_rows()) == {day: _expected(day) for day in TURNOVER}


def test_detail_daily_rows_carry_the_published_turnover_by_date(tmp_path, repo):
    svc = _service(tmp_path, repo)

    stock = svc._aggregate_daily(STOCK, 120)
    etf = svc._aggregate_daily(ETF, 120)

    assert stock.status == "available"
    assert [(row.date, row.turnover_rate) for row in stock.rows] == [
        (day.isoformat(), _expected(day)) for day in DAYS
    ]
    assert [row.turnover_rate for row in etf.rows] == [None] * len(DAYS)


def test_detail_daily_rows_without_a_repository_have_no_turnover(tmp_path):
    rows = _service(tmp_path, None)._aggregate_daily(STOCK, 120).rows

    assert [row.close for row in rows] == [1000.0] * len(DAYS)
    assert [row.turnover_rate for row in rows] == [None] * len(DAYS)


def test_a_turnover_read_failure_keeps_the_daily_rows(tmp_path, repo, monkeypatch):
    monkeypatch.setattr(repo, "get_daily", MagicMock(side_effect=OSError("disk gone")))

    daily = _service(tmp_path, repo)._aggregate_daily(STOCK, 120)

    assert daily.status == "available"
    assert [row.turnover_rate for row in daily.rows] == [None] * len(DAYS)


def test_turnover_is_optional_on_the_daily_row():
    row = TaiwanDailyRow(date="2026-09-21", open=1, high=1, low=1, close=1, volume=1)

    assert row.turnover_rate is None


class _FakeRepository:
    """Serves the published turnover of STOCK; records the symbols it was asked for."""

    def __init__(self) -> None:
        self.symbols: list[str] = []

    def get_daily(self, symbol, start, end, columns=None):
        self.symbols.append(symbol)
        return pl.DataFrame(
            {"date": list(TURNOVER), "turnover_rate": list(TURNOVER.values())},
            schema={"date": pl.Date, "turnover_rate": pl.Float64},
        )


def _detail_client(monkeypatch, service: TaiwanStockDetailService, repository=None) -> TestClient:
    """The Taiwan router on an app whose state holds *repository*; sections other than daily are stubbed."""
    monkeypatch.setattr(detail_service, "_detail_service", service)
    monkeypatch.setattr(service, "_fetch_quote", lambda symbol: None)
    monkeypatch.setattr(
        service, "_aggregate_institutional", lambda sym: service._empty_institutional()
    )
    monkeypatch.setattr(service, "_aggregate_margin", lambda sym: service._empty_margin())
    app = FastAPI()
    app.include_router(taiwan_router)
    if repository is not None:
        app.state.repo = repository
    return TestClient(app)


def test_the_detail_endpoint_reads_turnover_through_the_app_repository(tmp_path, monkeypatch):
    repository = _FakeRepository()
    client = _detail_client(monkeypatch, _service(tmp_path, None), repository)

    response = client.get(f"/api/taiwan/stocks/{STOCK}")

    assert response.status_code == 200, response.text
    rows = response.json()["daily_history"]["rows"]
    assert [(row["date"], row["turnover_rate"]) for row in rows] == [
        (day.isoformat(), TURNOVER.get(day)) for day in DAYS
    ]
    assert repository.symbols == [STOCK]


def test_the_detail_endpoint_without_an_app_repository_keeps_the_daily_rows(tmp_path, monkeypatch):
    client = _detail_client(monkeypatch, _service(tmp_path, None))

    response = client.get(f"/api/taiwan/stocks/{STOCK}")

    assert response.status_code == 200, response.text
    rows = response.json()["daily_history"]["rows"]
    assert [row["close"] for row in rows] == [1000.0] * len(DAYS)
    assert [row["turnover_rate"] for row in rows] == [None] * len(DAYS)
