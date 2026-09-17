"""修正 / 补全 A 股日K数据(kline_daily / kline_etf_daily / kline_index_daily)。

其后端 daily_pipeline.run_now() 随 A 股盘后管道一并下线。这些表 Taiwan
部署从不写入(Taiwan 数据走 taiwan/daily_store.py + enriched_refresh.py,
直接发布到 kline_daily_enriched, 不经过这几张表), 且调用方
data_integrity.launch_integrity_repair() 本就以 Cap.KLINE_DAILY_BATCH
(A 股批量能力) 为前置门禁, Taiwan 部署不会持有该能力, 故此函数实际不会
被触发。保留为显式软失败, 避免残留 import 造成难以定位的运行期错误。
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from datetime import date

from app.tickflow.capabilities import CapabilitySet
from app.tickflow.repository import KlineRepository

logger = logging.getLogger(__name__)


def run_repair_daily(
    repo: KlineRepository,
    capset: CapabilitySet,
    start_date: date,
    end_date: date | None = None,
    on_progress: Callable | None = None,
) -> dict:
    """A 股日K修正管道已下线, 直接软失败。"""
    logger.warning("repair_daily: A-share daily pipeline has been removed, skipping")
    return {"error": "A 股日K修正管道已下线"}
