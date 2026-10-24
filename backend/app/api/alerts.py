"""告警触发记录 API — 查询/清空 alerts.jsonl。"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from app.services import alert_store

router = APIRouter(prefix="/api/alerts", tags=["alerts"])


def _data_dir(request: Request) -> Path:
    return request.app.state.repo.store.data_dir


@router.get("")
def list_alerts(
    request: Request,
    days: int = 7,
    limit: int = 5000,
    source: str | None = None,
    type: str | None = None,
    ext_columns: str | None = None,
):
    """查询触发记录 (时间倒序)。

    ext_columns: 逗号分隔的 "configId.fieldName", 传入后按 symbol 富化行业/概念等 ext 字段,
    每条记录附带 {configId}__{fieldName} 键 (与 watchlist/screener 一致)。
    """
    events = alert_store.list_recent(
        _data_dir(request), days=days, limit=limit, source=source, type=type,
    )
    if ext_columns and events:
        try:
            from app.api.screener import _load_ext_value_maps, _rows_with_ext
            repo = request.app.state.repo
            value_maps = _load_ext_value_maps(repo, ext_columns)
            if value_maps:
                events = _rows_with_ext(events, value_maps)
        except Exception:  # noqa: BLE001
            pass
    total = alert_store.count(_data_dir(request))
    return {"alerts": events, "total": total}


@router.delete("")
def clear_alerts(request: Request):
    """清空全部触发记录。"""
    n = alert_store.clear(_data_dir(request))
    return {"ok": True, "cleared": n}


@router.delete("/{alert_id}")
def delete_alert(alert_id: str, request: Request):
    """删除单条触发记录 (按记录 id)。

    纯数字且无对应 id 时按旧客户端的 ts 处理, 同 ts 多条则返回 409 而不删除。
    """
    data_dir = _data_dir(request)
    if alert_store.delete_one(data_dir, alert_id):
        return {"ok": True}
    if alert_id.isascii() and alert_id.isdigit():
        outcome = alert_store.delete_by_unique_ts(data_dir, int(alert_id))
        if outcome == "deleted":
            return {"ok": True}
        if outcome == "ambiguous":
            raise HTTPException(status_code=409, detail="多条记录共用此时间戳, 请按 id 删除")
    raise HTTPException(status_code=404, detail="记录不存在")
