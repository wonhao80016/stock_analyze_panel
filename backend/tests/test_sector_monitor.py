from __future__ import annotations

from types import SimpleNamespace

import polars as pl
import pytest

from app.services import sector_monitor
from app.services.ext_data import ExtConfig, ExtConfigStore, ExtField
from app.services.sector_monitor import SectorMonitorService
from app.strategy import monitor_rules
from app.strategy.monitor import MonitorRuleEngine


class _Repo:
    def __init__(self, data_dir, indices: pl.DataFrame | None = None):
        self.store = SimpleNamespace(data_dir=data_dir)
        self._indices = indices if indices is not None else pl.DataFrame()

    def get_index_instruments(self) -> pl.DataFrame:
        return self._indices


def _index_target(symbol: str, name: str) -> dict:
    return {
        "key": f"index:{symbol}",
        "kind": "index",
        "name": name,
        "symbol": symbol,
    }


def _sector_rule(targets: list[dict], **overrides) -> dict:
    rule = {
        "id": "r_sector",
        "name": "板块监控",
        "enabled": True,
        "type": "sector",
        "scope": "all",
        "sector_kind": targets[0]["kind"],
        "sector_targets": targets,
        "sector_trigger": "change_pct",
        "direction": "up",
        "threshold_pct": 1.0,
        "window_minutes": 5,
        "cooldown_seconds": 0,
        "severity": "info",
    }
    rule.update(overrides)
    return monitor_rules.normalize(rule)


def test_validate_accepts_sector_rule_and_rejects_mixed_target_kinds():
    rule = _sector_rule([_index_target("t00.TWSE", "發行量加權股價指數")])
    monitor_rules.validate(rule)

    mixed = _sector_rule([
        _index_target("t00.TWSE", "發行量加權股價指數"),
        {
            "key": "concept:test:field:人工智能",
            "kind": "concept",
            "name": "人工智能",
            "source_id": "test",
            "field": "field",
            "value": "人工智能",
        },
    ])
    try:
        monitor_rules.validate(mixed)
    except ValueError as exc:
        assert "類型" in str(exc)
    else:
        raise AssertionError("混合板块类型必须被拒绝")


def test_dimension_values_preserve_names_with_spaces_and_filter_nulls(tmp_path):
    service = SectorMonitorService(_Repo(tmp_path))

    assert service._dimension_values("中国AI 50;6G概念") == ["中国AI 50", "6G概念"]
    assert service._dimension_values("nan") == []
    assert service._dimension_values(float("nan")) == []
    assert service._industry_paths("电子-半导体-数字芯片设计")[-1] == (
        "电子-半导体-数字芯片设计", 3, "电子 / 半导体 / 数字芯片设计",
    )


def test_index_targets_are_evaluated_independently(tmp_path):
    repo = _Repo(tmp_path)
    service = SectorMonitorService(repo)
    engine = MonitorRuleEngine()
    engine.set_sector_monitor_service(service)
    twse = _index_target("t00.TWSE", "發行量加權股價指數")
    tpex = _index_target("o00.TPEX", "櫃買指數")
    engine.set_rules([_sector_rule([twse, tpex])])

    first = pl.DataFrame({
        "symbol": ["t00.TWSE", "o00.TPEX"],
        "name": ["發行量加權股價指數", "櫃買指數"],
        "close": [3000.0, 2000.0],
        "change_pct": [0.8, 0.9],
    })
    assert engine.evaluate_sectors(pl.DataFrame(), first, now=1000.0) == []

    second = first.with_columns(
        pl.Series("change_pct", [1.2, 0.95]),
    )
    events = engine.evaluate_sectors(pl.DataFrame(), second, now=1006.0)

    assert [event["sector_name"] for event in events] == ["發行量加權股價指數"]
    assert events[0]["change_pct"] == 0.012


def test_index_availability_updates_when_realtime_pool_changes(tmp_path, monkeypatch):
    selected = ["t00.TWSE"]
    monkeypatch.setattr(sector_monitor.preferences, "get_realtime_pull_index", lambda: True)
    monkeypatch.setattr(sector_monitor.preferences, "get_realtime_index_mode", lambda: "core")
    monkeypatch.setattr(sector_monitor.preferences, "get_realtime_index_symbols", lambda: selected)
    service = SectorMonitorService(_Repo(tmp_path))

    first = {target["symbol"]: target for target in service.list_targets()["index"]}
    assert first["t00.TWSE"]["available"] is True
    assert first["o00.TPEX"]["available"] is False
    initial_quote = pl.DataFrame({"symbol": ["t00.TWSE"], "change_pct": [0.2]})
    service.build_snapshots(pl.DataFrame(), initial_quote, [first["t00.TWSE"]], {5}, now=1000.0)

    selected[:] = ["o00.TPEX"]
    second = {target["symbol"]: target for target in service.list_targets()["index"]}
    assert second["t00.TWSE"]["available"] is False
    assert second["o00.TPEX"]["available"] is True
    changed_quote = pl.DataFrame({"symbol": ["t00.TWSE"], "change_pct": [1.3]})
    snapshot = service.build_snapshots(
        pl.DataFrame(), changed_quote, [second["t00.TWSE"]], {5}, now=1300.0,
    )
    assert snapshot["index:t00.TWSE"]["window_changes"][5] is None


def test_concept_snapshot_uses_member_average_and_full_window(tmp_path):
    config = ExtConfig(
        id="concept_test",
        label="概念测试",
        mode="snapshot",
        fields=[
            ExtField("symbol", "string", "标的代码"),
            ExtField("concept", "string", "所属概念"),
        ],
    )
    ExtConfigStore(tmp_path).upsert(config)
    ext_dir = tmp_path / "ext_data" / config.id
    pl.DataFrame({
        "symbol": ["A", "B", "C", "D", "E"],
        "concept": ["人工智能", "人工智能", "人工智能", "人工智能", "人工智能"],
    }).write_parquet(ext_dir / "part.parquet")

    service = SectorMonitorService(_Repo(tmp_path))
    target = next(
        target for target in service.list_targets()["concept"]
        if target["name"] == "人工智能"
    )
    first = pl.DataFrame({
        "symbol": ["A", "B", "C", "D", "E"],
        "name": ["甲", "乙", "丙", "丁", "戊"],
        "close": [10.0] * 5,
        "change_pct": [0.01, 0.02, 0.03, -0.01, 0.0],
    })
    snapshots = service.build_snapshots(first, pl.DataFrame(), [target], {5}, now=1000.0)
    assert snapshots[target["key"]]["change_pct"] == pytest.approx(0.01)
    assert snapshots[target["key"]]["coverage_ratio"] == 1.0
    assert snapshots[target["key"]]["window_changes"][5] is None

    second = first.with_columns((pl.col("change_pct") + 0.01).alias("change_pct"))
    too_early = service.build_snapshots(second, pl.DataFrame(), [target], {5}, now=1240.0)
    assert too_early[target["key"]]["window_changes"][5] is None

    unrelated = ExtConfig(
        id="hot_test",
        label="热度测试",
        mode="snapshot",
        fields=[
            ExtField("symbol", "string", "标的代码"),
            ExtField("heat", "float", "市场热度"),
        ],
    )
    ExtConfigStore(tmp_path).upsert(unrelated)
    unrelated_dir = tmp_path / "ext_data" / unrelated.id
    pl.DataFrame({"symbol": ["A"], "heat": [1.0]}).write_parquet(unrelated_dir / "part.parquet")

    complete = service.build_snapshots(second, pl.DataFrame(), [target], {5}, now=1300.0)
    assert complete[target["key"]]["window_changes"][5] == pytest.approx(0.01)


def test_momentum_rule_triggers_after_complete_window(tmp_path):
    service = SectorMonitorService(_Repo(tmp_path))
    engine = MonitorRuleEngine()
    engine.set_sector_monitor_service(service)
    target = _index_target("t00.TWSE", "發行量加權股價指數")
    engine.set_rules([_sector_rule(
        [target],
        sector_trigger="momentum",
        threshold_pct=1.0,
        window_minutes=5,
    )])
    start = pl.DataFrame({
        "symbol": ["t00.TWSE"],
        "name": ["發行量加權股價指數"],
        "close": [3000.0],
        "change_pct": [0.2],
    })
    assert engine.evaluate_sectors(pl.DataFrame(), start, now=1000.0) == []

    early = start.with_columns(pl.lit(1.3).alias("change_pct"))
    assert engine.evaluate_sectors(pl.DataFrame(), early, now=1240.0) == []

    events = engine.evaluate_sectors(pl.DataFrame(), early, now=1300.0)
    assert len(events) == 1
    assert events[0]["type"] == "sector_momentum_up"
    assert events[0]["window_change_pct"] == pytest.approx(0.011)
