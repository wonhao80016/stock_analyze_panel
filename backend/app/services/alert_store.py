"""告警触发记录存储 — JSONL 追加写 + 滚动清理。

职责:
  - 把每次触发的 AlertEvent 追加写入 data/user_data/alerts.jsonl
  - 提供查询 (按来源/类型过滤、时间倒序、限量)
  - 滚动清理: 保留近 N 天 + 上限 M 条 (取交集)

设计:
  - JSONL 每行一个 JSON 对象,便于增量追加和流式读取
  - 每条记录以 "id" 为唯一键; 同一轮触发的多条记录 ts 相同, 不能用 ts 识别
  - 清理策略: 追加后按需 prune (按 ts 删旧),避免文件无限膨胀
  - 读时全量加载到内存过滤 (记录量受上限约束, 5000 条量级无压力)
"""
from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
import uuid
from pathlib import Path
from typing import Literal

from app.taiwan.universe.service import write_atomically

logger = logging.getLogger(__name__)

# 保留策略
MAX_DAYS = 7
MAX_RECORDS = 5000
# 每隔多少次写入触发一次清理 (避免每次写都 prune)
PRUNE_EVERY = 20

_lock = threading.Lock()
_write_count = 0


def _path(data_dir: Path) -> Path:
    p = data_dir / "user_data" / "alerts.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _with_id(event: dict) -> dict:
    event.setdefault("id", uuid.uuid4().hex)
    return event


def _legacy_id(event: dict, seen: dict[str, int]) -> str:
    """Stable id for a row written before ids existed: content hash plus its occurrence index."""
    digest = hashlib.sha1(
        json.dumps(event, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()[:16]
    n = seen.get(digest, 0)
    seen[digest] = n + 1
    return f"legacy-{digest}-{n}"


def _read_locked(p: Path) -> list[dict]:
    """(调用方需持锁) 按文件顺序解析全部记录, 无 id 的旧记录补上稳定 id (不写回)。"""
    out: list[dict] = []
    seen: dict[str, int] = {}
    with p.open("r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue
            if not isinstance(ev, dict):
                continue
            if not isinstance(ev.get("id"), str) or not ev["id"]:
                ev["id"] = _legacy_id(ev, seen)
            out.append(ev)
    return out


def _rewrite_locked(p: Path, events: list[dict]) -> None:
    """(调用方需持锁) 原子重写整个文件; 旧记录的派生 id 随之落盘, 保持不变。"""
    def write(tmp: Path) -> None:
        with tmp.open("w", encoding="utf-8") as f:
            for ev in events:
                f.write(json.dumps(ev, ensure_ascii=False) + "\n")

    write_atomically(p, write)


def append(data_dir: Path, event: dict) -> None:
    """追加一条触发记录。event 应含 ts(毫秒)、rule_id、source 等字段; 缺 id 时就地补上。"""
    line = json.dumps(_with_id(event), ensure_ascii=False)
    with _lock:
        p = _path(data_dir)
        with p.open("a", encoding="utf-8") as f:
            f.write(line + "\n")
        global _write_count
        _write_count += 1
        if _write_count >= PRUNE_EVERY:
            _write_count = 0
            _prune_locked(p)


def append_many(data_dir: Path, events: list[dict]) -> None:
    """批量追加; 缺 id 的事件就地补上。"""
    if not events:
        return
    with _lock:
        p = _path(data_dir)
        with p.open("a", encoding="utf-8") as f:
            for ev in events:
                f.write(json.dumps(_with_id(ev), ensure_ascii=False) + "\n")
        global _write_count
        _write_count += len(events)
        if _write_count >= PRUNE_EVERY:
            _write_count = 0
            _prune_locked(p)


def list_recent(
    data_dir: Path,
    days: int = MAX_DAYS,
    limit: int = MAX_RECORDS,
    source: str | None = None,
    type: str | None = None,
) -> list[dict]:
    """读取近 N 天记录,按时间倒序,支持按 source/type 过滤。

    持锁读: prune/delete/clear 会整文件重写, 无锁读可能读到截断内容。
    """
    cutoff = (time.time() - days * 86400) * 1000  # 毫秒
    p = _path(data_dir)
    if not p.exists():
        return []
    try:
        with _lock:
            events = _read_locked(p)
    except Exception as e:
        logger.warning("alert_store read failed: %s", e)
        return []
    numeric = [ev for ev in events if isinstance(ev.get("ts", 0), (int, float)) and not isinstance(ev.get("ts"), bool)]
    if len(numeric) != len(events):
        logger.warning("alert_store skipped %d records without a numeric ts", len(events) - len(numeric))
    out = [
        ev for ev in numeric
        if ev.get("ts", 0) >= cutoff
        and (not source or ev.get("source") == source)
        and (not type or ev.get("type") == type)
    ]
    # 时间倒序 + 截断
    out.sort(key=lambda x: x.get("ts", 0), reverse=True)
    return out[:limit]


def clear(data_dir: Path) -> int:
    """清空全部记录,返回清除的条数。"""
    with _lock:
        p = _path(data_dir)
        if not p.exists():
            return 0
        count = 0
        try:
            with p.open("r", encoding="utf-8") as f:
                count = sum(1 for line in f if line.strip())
        except Exception:
            pass
        _rewrite_locked(p, [])
        return count


def delete_one(data_dir: Path, alert_id: str) -> bool:
    """删除指定 id 的单条记录,返回是否删除成功。"""
    with _lock:
        p = _path(data_dir)
        if not p.exists():
            return False
        try:
            events = _read_locked(p)
        except Exception as e:
            logger.warning("alert_store delete_one read failed: %s", e)
            return False
        kept = [ev for ev in events if ev["id"] != alert_id]
        if len(kept) == len(events):
            return False
        try:
            _rewrite_locked(p, kept)
        except Exception as e:
            logger.warning("alert_store delete_one write failed: %s", e)
            return False
        return True


def delete_by_unique_ts(data_dir: Path, ts: int) -> Literal["deleted", "missing", "ambiguous"]:
    """按 ts 删除, 仅供未带 id 的旧调用方; 多条同 ts 时拒绝删除, 避免删错记录。"""
    with _lock:
        p = _path(data_dir)
        if not p.exists():
            return "missing"
        try:
            events = _read_locked(p)
        except Exception as e:
            logger.warning("alert_store delete_by_unique_ts read failed: %s", e)
            return "missing"
        matches = [ev for ev in events if ev.get("ts") == ts]
        if not matches:
            return "missing"
        if len(matches) > 1:
            return "ambiguous"
        try:
            _rewrite_locked(p, [ev for ev in events if ev is not matches[0]])
        except Exception as e:
            logger.warning("alert_store delete_by_unique_ts write failed: %s", e)
            return "missing"
        return "deleted"


def count(data_dir: Path) -> int:
    """返回当前记录总数。持锁读, 防与整文件重写并发。"""
    p = _path(data_dir)
    if not p.exists():
        return 0
    try:
        with _lock, p.open("r", encoding="utf-8") as f:
            return sum(1 for line in f if line.strip())
    except Exception:
        return 0


def _prune_locked(p: Path) -> None:
    """(调用方需持锁) 保留近 MAX_DAYS 天 + 上限 MAX_RECORDS 条。"""
    cutoff = (time.time() - MAX_DAYS * 86400) * 1000
    try:
        kept = [ev for ev in _read_locked(p) if ev.get("ts", 0) >= cutoff]
    except FileNotFoundError:
        return
    except Exception as e:
        logger.warning("alert_store prune read failed: %s", e)
        return
    # 上限截断 (保留最新的)
    if len(kept) > MAX_RECORDS:
        kept.sort(key=lambda x: x.get("ts", 0))
        kept = kept[-MAX_RECORDS:]
    try:
        _rewrite_locked(p, kept)
    except Exception as e:
        logger.warning("alert_store prune write failed: %s", e)
