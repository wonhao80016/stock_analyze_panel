"""回归测试: 实时指数 merge 不截断盘后管道写入的全量分区 (PR #46 问题 3)。"""
from datetime import date

import polars as pl
import pytest

from app.repository import DataStore, KlineRepository
from app.taiwan.daily_store import TaiwanDailyStore

DAY = date(2026, 7, 28)


@pytest.fixture(autouse=True)
def _published_day(tmp_path, monkeypatch):
    """Live writes reach disk only for a day the exchange has published (_live_write_allowed)."""
    from app.config import settings

    monkeypatch.setattr(settings, "data_dir", tmp_path)
    TaiwanDailyStore().write_batch(pl.DataFrame([{
        "symbol": "2330.TWSE", "date": DAY, "open": 1.0, "high": 1.0, "low": 1.0,
        "close": 1.0, "volume": 1.0, "amount": 1.0, "quote_ts": None,
    }]))


def _enriched_row(symbol: str, close: float, dt: date) -> dict:
    return {
        "symbol": symbol, "date": dt,
        "open": close, "high": close, "low": close, "close": close,
        "volume": 1000, "amount": 10000.0,
        "quote_ts": 1753700400000,
    }


def test_merge_live_enriched_preserves_full_index_partition(tmp_path):
    """盘后管道 flush 写入全量指数后, 实时 merge 部分指数不丢已有数据。"""
    repo = KlineRepository(DataStore(tmp_path))
    dt = DAY

    # 模拟盘后管道: flush 写入全量 2 只指数
    full_df = pl.DataFrame([
        _enriched_row("t00.TWSE", 23000.0, dt),
        _enriched_row("o00.TPEX", 260.0, dt),
    ])
    repo.flush_live_enriched_asset("index", full_df)

    # 模拟实时刷新: 只 merge 加权指数 1 只 (价格更新)
    partial_df = pl.DataFrame([
        _enriched_row("t00.TWSE", 23001.0, dt),
    ])
    repo.merge_live_enriched_asset("index", partial_df)

    # 验证: 分区文件仍有 2 只指数, t00.TWSE 价格已更新, 櫃買指数未丢失
    out = tmp_path / "kline_index_enriched" / f"date={dt.isoformat()}" / "part.parquet"
    result = pl.read_parquet(out)
    assert len(result) == 2, f"merge 后分区应有 2 只指数, 实际 {len(result)}"

    taiex = result.filter(pl.col("symbol") == "t00.TWSE")
    assert taiex["close"][0] == 23001.0, "merge 应更新 t00.TWSE 价格"

    otc = result.filter(pl.col("symbol") == "o00.TPEX")
    assert otc["close"][0] == 260.0, "o00.TPEX 不应被 merge 覆盖"
