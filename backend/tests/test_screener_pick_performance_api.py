"""Pick performance endpoints over snapshots written by record() and synthetic daily K under tmp_path."""
from __future__ import annotations

from datetime import date, datetime, time, timedelta
from pathlib import Path
from types import SimpleNamespace

import polars as pl
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.api import screener as screener_api
from app.services import strategy_pick_history as sph
from app.services.strategy_pick_performance import CORPORATE_ACTION_SOURCES, HORIZONS
from app.taiwan.corporate_actions import TaiwanCorporateActionStore, corporate_action_schema
from app.taiwan.daily_store import TaiwanDailyStore
from app.taiwan.enrichment.corporate_actions import EXDIV
from app.taiwan.realtime.calendar import TAIPEI_TZ

D = [date(2026, 9, 1) + timedelta(days=i) for i in range(21)]
SYM = "2330.TWSE"
OTC = "6510.TPEX"
LATE_SYM = "2317.TWSE"
FILLERS = ("1101.TWSE", "6488.TPEX")
NAMES = {SYM: "Alpha", OTC: "Beta"}


def _bar(symbol: str, d: date, o: float, c: float) -> dict:
    return {"symbol": symbol, "date": d, "open": o, "high": max(o, c), "low": min(o, c), "close": c, "volume": 1000.0}


def _write_market(data_dir: Path) -> None:
    rows = [_bar(SYM, D[0], 100.0, 100.0)]
    rows += [_bar(SYM, D[k], 100.0 + k - 1, 100.0 + k) for k in range(1, len(D))]
    rows += [_bar(OTC, d, 50.0, 50.0) for i, d in enumerate(D) if i not in (5, 10)]
    rows += [_bar(LATE_SYM, d, 80.0, 80.0) for d in D]
    rows += [_bar(s, d, 10.0, 10.0) for i, d in enumerate(D) for s in FILLERS if (s, i) != ("6488.TPEX", 5)]
    frame = pl.DataFrame(rows).with_columns(
        pl.lit(1.0).alias("amount"), pl.lit(None, dtype=pl.Int64).alias("quote_ts")
    )
    TaiwanDailyStore(data_dir / "taiwan" / "daily").write_batch(frame)


def _write_adjustment(data_dir: Path) -> TaiwanCorporateActionStore:
    store = TaiwanCorporateActionStore(data_dir / "taiwan" / "corporate_actions")
    event = {
        "symbol": "9999.TWSE", "date": date(2020, 6, 1), "kind": EXDIV, "pre_close": 10.0, "ref_price": 9.0,
        "limit_up": 9.9, "limit_down": 8.1, "open_ref": 9.0, "ex_factor": None, "official_label": None,
        "provider": "test", "source": "twse:twt49u", "source_url": None, "retrieved_at": None,
        "trade_date": date(2020, 6, 1), "status": "ok",
    }
    store.upsert(pl.DataFrame([event], schema=corporate_action_schema()))
    store.record_fetch(CORPORATE_ACTION_SOURCES, date(2020, 1, 1), date(2030, 1, 1), datetime(2026, 9, 1, 18))
    return store


def _status(exchanges=("TWSE", "TPEX"), state="ok", signal_date=D[0]) -> sph.InputStatus:
    return sph.InputStatus(state=state, rows=10, exchanges_at_t=frozenset(exchanges), dates=(signal_date,))


def _record(data_dir: Path, signal_date: date, sid: str, symbols, now: datetime, **report) -> None:
    results = {
        sid: {
            "total": len(symbols),
            "as_of": signal_date.isoformat(),
            "rows": [
                {"symbol": s, "date": signal_date, "score": 1.0, "raw_close": 1.0} for s in symbols
            ],
        }
    }
    run_report = sph.RunReport(
        current=report.get("current", _status(signal_date=signal_date)),
        history=report.get("history"),
        input_groups={sid: frozenset({sph.CURRENT})},
        fingerprints={sid: f"fp-{sid}"},
    )
    out = sph.record(data_dir, signal_date, results, run_report, "16:30", 1.0, now)
    assert out.written == (sid,)


def _at(d: date, hour: int) -> datetime:
    return datetime.combine(d, time(hour), TAIPEI_TZ)


def _snapshots(data_dir: Path) -> None:
    """s_main is on time and complete; the other three are each excluded for one reason."""
    _record(data_dir, D[0], "s_main", [SYM, OTC], _at(D[0], 16), history=_status(state="error"))
    _record(data_dir, D[0], "s_half", [SYM], _at(D[0], 17), current=_status(exchanges=("TWSE",)))
    _record(data_dir, D[1], "s_late", [LATE_SYM], _at(D[2], 10))
    _record(data_dir, D[3], "s_broken", [SYM], _at(D[3], 16))
    runs_path = data_dir / "user_data" / "strategy_picks" / f"date={D[3].isoformat()}" / "runs.parquet"
    runs = pl.read_parquet(runs_path)
    runs.with_columns(pl.col("rows_written") + 1).write_parquet(runs_path)


def _profile(symbols: list[str]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "symbol": symbols,
            "limit_pct": [None if s == OTC else 0.10 for s in symbols],
            "is_etf_tick": [False] * len(symbols),
        },
        schema={"symbol": pl.Utf8, "limit_pct": pl.Float64, "is_etf_tick": pl.Boolean},
    )


@pytest.fixture
def client(tmp_path, monkeypatch):
    def forbidden(*_args, **_kwargs):
        raise AssertionError("global data root used")

    master = SimpleNamespace(
        get_instrument=lambda s: SimpleNamespace(name=NAMES[s]) if s in NAMES else None
    )
    monkeypatch.setattr("app.taiwan.data_root.taiwan_data_root", forbidden)
    monkeypatch.setattr("app.taiwan.universe.get_security_master", lambda: master)
    monkeypatch.setattr(screener_api, "_pick_limit_profile", _profile)
    monkeypatch.setattr(
        screener_api,
        "_current_strategy_versions",
        lambda _request, ids: {sid: sph.CurrentVersion(None, f"fp-{sid}", True) for sid in ids},
    )
    app = FastAPI()
    app.include_router(screener_api.router)
    app.state.repo = SimpleNamespace(store=SimpleNamespace(data_dir=tmp_path))
    return TestClient(app)


@pytest.fixture
def populated(tmp_path):
    _write_market(tmp_path)
    _write_adjustment(tmp_path)
    _snapshots(tmp_path)
    return tmp_path


def _by_id(payload: dict) -> dict[str, dict]:
    return {s["strategy_id"]: s for s in payload["strategies"]}


def test_summary_chains_recorded_snapshots_into_decimal_statistics(client, populated):
    response = client.get("/api/screener/pick-performance")
    assert response.status_code == 200
    payload = response.json()

    assert payload["method"]["costs_included"] is False
    assert payload["method"]["slippage_included"] is False
    assert payload["method"]["exit_fill_simulated"] is False
    assert payload["method"]["return_unit"] == "decimal"
    assert payload["adjustment"] == {"status": "ok", "reason": None, "covered_through": "2030-01-01"}
    assert payload["first_signal_date"] == D[0].isoformat()
    assert payload["last_signal_date"] == D[3].isoformat()
    assert payload["prices_through"] == D[-1].isoformat()

    main = _by_id(payload)["s_main"]
    assert main["signal_count"] == 2 and main["signal_days"] == 1
    assert main["filled_count"] == 2 and main["completed_count"] == 2
    assert main["limit_check_unknown"] == 1
    assert main["history_degraded_count"] == 2
    assert main["excluded"] == {"manifest_missing": 0, "recorded_late": 0, "universe_incomplete": 0}
    assert "stale_exit_count" not in main

    one = main["horizons"]["1"]
    assert one["sample_count"] == 2 and one["sample_days"] == 1
    assert one["mean_return"] == pytest.approx(0.005)
    assert one["median_return"] == pytest.approx(0.005)
    assert one["positive_ratio"] == pytest.approx(0.5)
    five = main["horizons"]["5"]
    assert five["sample_count"] == 0 and five["data_incomplete"] == 2
    ten = main["horizons"]["10"]
    assert ten["stale_exit_count"] == 1 and ten["sample_count"] == 2
    assert ten["mean_return"] == pytest.approx(0.05)
    twenty = main["horizons"]["20"]
    assert twenty["mean_return"] == pytest.approx(0.10)
    assert twenty["pending_count"] == 0 and twenty["prices_incomplete"] == 0


def test_summary_counts_each_exclusion_without_sampling_it(client, populated):
    payload = client.get("/api/screener/pick-performance").json()
    stats = _by_id(payload)
    # A run without a readable manifest has no known version, so its pick is legacy.
    stats["s_broken"] = {s["strategy_id"]: s for s in payload["legacy_strategies"]}["s_broken"]

    assert stats["s_half"]["excluded"]["universe_incomplete"] == 1
    assert stats["s_late"]["excluded"]["recorded_late"] == 1
    assert stats["s_broken"]["excluded"]["manifest_missing"] == 1
    for sid in ("s_half", "s_late", "s_broken"):
        assert stats[sid]["signal_count"] == 1
        assert stats[sid]["filled_count"] == 0
        assert stats[sid]["history_degraded_count"] == 0
        assert all(h["sample_count"] == 0 for h in stats[sid]["horizons"].values())


def test_detail_lists_one_strategy_with_per_horizon_reasons(client, populated):
    response = client.get("/api/screener/pick-performance/s_main")
    assert response.status_code == 200
    payload = response.json()
    assert payload["strategy_id"] == "s_main"
    assert payload["total"] == 2 and payload["signal_days"] == 1
    assert payload["adjustment"]["status"] == "ok"
    first, second = payload["rows"]
    assert (first["symbol"], first["name"], first["rank"]) == (SYM, "Alpha", 1)
    assert (second["symbol"], second["name"]) == (OTC, "Beta")
    assert first["signal_date"] == D[0].isoformat() and first["entry_date"] == D[1].isoformat()
    assert first["entry_price_raw"] == 100.0
    assert first["limit_check"] == "checked" and second["limit_check"] == "unknown"
    assert first["returns"]["1"] == {
        "value": pytest.approx(0.01), "reason": None, "stale_exit": False, "stale_days": 0,
        "benchmark_return": None, "excess_return": None,
    }
    assert first["returns"]["1"]["value"] != pytest.approx(1.0)
    assert first["returns"]["5"] == {
        "value": None, "reason": "data_incomplete", "stale_exit": False, "stale_days": 0,
        "benchmark_return": None, "excess_return": None,
    }
    assert second["returns"]["10"] == {
        "value": pytest.approx(0.0), "reason": None, "stale_exit": True, "stale_days": 1,
        "benchmark_return": None, "excess_return": None,
    }
    assert first["history_degraded"] is True
    assert first["excluded_reason"] is None and first["manifest_missing"] is False


def test_detail_marks_pending_horizons_and_honours_limit(client, populated):
    late = client.get("/api/screener/pick-performance/s_late").json()
    row = late["rows"][0]
    assert row["recorded_late"] is True and row["excluded_reason"] == "recorded_late"
    assert row["returns"]["20"]["reason"] == "pending"

    limited = client.get("/api/screener/pick-performance/s_main", params={"limit": 1}).json()
    assert limited["total"] == 2 and len(limited["rows"]) == 1


@pytest.mark.parametrize(("limit", "status"), [("0", 422), ("2001", 422), ("abc", 422), ("1", 200), ("2000", 200)])
def test_detail_limit_bounds(client, populated, limit, status):
    response = client.get("/api/screener/pick-performance/s_main", params={"limit": limit})
    assert response.status_code == status
    if status == 200:
        assert len(response.json()["rows"]) == min(int(limit), 2)


def test_unknown_strategy_returns_no_rows(client, populated):
    response = client.get("/api/screener/pick-performance/nope")
    assert response.status_code == 200
    payload = response.json()
    assert payload["rows"] == [] and payload["total"] == 0


def test_no_snapshot_yet_gives_empty_payloads(client, tmp_path):
    summary = client.get("/api/screener/pick-performance")
    assert summary.status_code == 200
    payload = summary.json()
    assert payload["strategies"] == []
    assert payload["first_signal_date"] is None and payload["prices_through"] is None
    detail = client.get("/api/screener/pick-performance/s_main").json()
    assert detail["rows"] == [] and detail["total"] == 0
    assert list(tmp_path.iterdir()) == []


def test_unavailable_adjustment_is_200_with_reason_and_null_returns(client, populated):
    TaiwanCorporateActionStore(populated / "taiwan" / "corporate_actions").path.unlink()

    summary = client.get("/api/screener/pick-performance")
    assert summary.status_code == 200
    payload = summary.json()
    assert payload["adjustment"] == {"status": "unavailable", "reason": "events_missing", "covered_through": None}
    main = _by_id(payload)["s_main"]
    assert main["signal_count"] == 2
    for horizon in main["horizons"].values():
        assert horizon["sample_count"] == 0 and horizon["mean_return"] is None

    rows = client.get("/api/screener/pick-performance/s_main").json()["rows"]
    for row in rows:
        assert row["limit_check"] == "unknown"
        assert all(row["returns"][str(k)]["value"] is None for k in HORIZONS)
        assert all(row["returns"][str(k)]["reason"] == "events_missing" for k in HORIZONS)


@pytest.mark.parametrize("path", ["/api/screener/pick-performance", "/api/screener/pick-performance/s_main"])
def test_unreadable_snapshot_is_503_with_a_stable_code(client, populated, path):
    part = populated / "user_data" / "strategy_picks" / f"date={D[0].isoformat()}" / "part.parquet"
    part.write_bytes(b"not parquet")

    response = client.get(path)
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "pick_history_unreadable"


def test_unreadable_prices_are_503_with_a_stable_code(client, populated, monkeypatch):
    def broken(*_args, **_kwargs):
        raise OSError("disk gone")

    monkeypatch.setattr(screener_api.strategy_pick_performance, "load_inputs", broken)
    response = client.get("/api/screener/pick-performance")
    assert response.status_code == 503
    assert response.json()["detail"]["code"] == "pick_prices_unreadable"


def test_limit_profile_failure_leaves_limit_unknown(monkeypatch):
    def broken(_symbols):
        raise RuntimeError("security master unavailable")

    monkeypatch.setattr("app.indicators.pipeline.taiwan_limit_profile", broken)
    profile = screener_api._pick_limit_profile([SYM])
    assert profile.to_dicts() == [{"symbol": SYM, "limit_pct": None, "is_etf_tick": False}]


def test_summary_and_detail_keep_strategy_versions_apart(client, populated, monkeypatch):
    def versions(_request, ids):
        # s_main's stored fingerprint no longer matches, so its picks are legacy.
        return {sid: sph.CurrentVersion(None, "fp-new" if sid == "s_main" else f"fp-{sid}", True) for sid in ids}

    monkeypatch.setattr(screener_api, "_current_strategy_versions", versions)
    payload = client.get("/api/screener/pick-performance").json()
    assert payload["version_counts"] == {"current": 2, "legacy": 3}
    current = _by_id(payload)["s_main"]
    assert current["signal_count"] == 0 and current["horizons"]["1"]["sample_count"] == 0
    legacy = {s["strategy_id"]: s for s in payload["legacy_strategies"]}
    assert set(legacy) == {"s_main", "s_broken"}
    assert legacy["s_main"]["signal_count"] == 2
    assert legacy["s_main"]["horizons"]["1"]["mean_return"] == pytest.approx(0.005)
    assert legacy["s_main"]["horizons"]["1"]["mean_excess_return"] is None

    def rows(version):
        return client.get("/api/screener/pick-performance/s_main", params={"version": version}).json()

    assert rows("current")["total"] == 0
    assert rows("legacy")["total"] == 2
    assert {r["version_status"] for r in rows("all")["rows"]} == {"legacy"}
    assert client.get("/api/screener/pick-performance/s_main", params={"version": "old"}).status_code == 422


def test_detail_reports_the_benchmark_return_of_each_pick(client, populated):
    rows = [_bar("0050.TWSE", d, 50.0 + i, 50.0 + i + 0.5) for i, d in enumerate(D)]
    frame = pl.DataFrame(rows).with_columns(pl.lit(1.0).alias("amount"), pl.lit(None, dtype=pl.Int64).alias("quote_ts"))
    TaiwanDailyStore(populated / "taiwan" / "daily").write_batch(frame)

    first = client.get("/api/screener/pick-performance/s_main").json()["rows"][0]
    assert first["version_status"] == "current" and first["strategy_version"] is None
    # Entry is the D[1] open of 51.0; T+1 exits at the D[1] close of 51.5 and T+20 at the D[20] close of 70.5.
    assert first["returns"]["1"]["benchmark_return"] == pytest.approx(51.5 / 51.0 - 1)
    assert first["returns"]["1"]["excess_return"] == pytest.approx(0.01 - (51.5 / 51.0 - 1))
    assert first["returns"]["20"]["benchmark_return"] == pytest.approx(70.5 / 51.0 - 1)

    summary = _by_id(client.get("/api/screener/pick-performance").json())["s_main"]["horizons"]["20"]
    assert summary["benchmark_sample_count"] == 2
    assert summary["mean_benchmark_return"] == pytest.approx(70.5 / 51.0 - 1)
