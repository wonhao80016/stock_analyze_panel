"""扩展数据服务 — 配置管理 + 文件解析 + Parquet 存储。"""
from __future__ import annotations

import copy
import json
import logging
import re
from datetime import date, datetime
from pathlib import Path
from typing import Literal

import polars as pl

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# 配置模型
# ---------------------------------------------------------------------------

class ExtField:
    """扩展字段定义。"""
    __slots__ = ("name", "dtype", "label")

    def __init__(self, name: str, dtype: str = "string", label: str = "") -> None:
        self.name = name
        self.dtype = dtype      # string | int | float | bool
        self.label = label or name

    def to_dict(self) -> dict:
        return {"name": self.name, "dtype": self.dtype, "label": self.label}

    @classmethod
    def from_dict(cls, d: dict) -> ExtField:
        return cls(d["name"], d.get("dtype", "string"), d.get("label", ""))


class PullConfig:
    """定时拉取配置。"""
    __slots__ = (
        "url", "method", "headers", "body", "response_path",
        "field_map", "schedule_minutes", "enabled",
        "last_run", "last_status", "last_message", "last_rows",
        "next_run", "time_window_start", "time_window_end",
    )

    def __init__(
        self,
        url: str = "",
        method: str = "GET",
        headers: dict[str, str] | None = None,
        body: str | None = None,
        response_path: str = "",
        field_map: dict[str, str] | None = None,
        schedule_minutes: int = 1440,
        enabled: bool = False,
        last_run: str | None = None,
        last_status: str | None = None,
        last_message: str | None = None,
        last_rows: int | None = None,
        next_run: str | None = None,
        time_window_start: str | None = None,
        time_window_end: str | None = None,
    ) -> None:
        self.url = url
        self.method = method              # GET | POST
        self.headers = headers or {}
        self.body = body                  # JSON string (POST body template)
        self.response_path = response_path  # dot-path to rows array, e.g. "data.list"
        self.field_map = field_map or {}    # external_name → config_field_name
        self.schedule_minutes = schedule_minutes
        self.enabled = enabled
        self.last_run = last_run
        self.last_status = last_status      # "success" | "error"
        self.last_message = last_message
        self.last_rows = last_rows
        self.next_run = next_run            # 下次预计运行 (ISO, 调度器写入)
        self.time_window_start = time_window_start  # 每日拉取窗口起始 "HH:MM", None=不限
        self.time_window_end = time_window_end      # 每日拉取窗口结束 "HH:MM", None=不限

    def to_dict(self) -> dict:
        return {
            "url": self.url,
            "method": self.method,
            "headers": self.headers,
            "body": self.body,
            "response_path": self.response_path,
            "field_map": self.field_map,
            "schedule_minutes": self.schedule_minutes,
            "enabled": self.enabled,
            "last_run": self.last_run,
            "last_status": self.last_status,
            "last_message": self.last_message,
            "last_rows": self.last_rows,
            "next_run": self.next_run,
            "time_window_start": self.time_window_start,
            "time_window_end": self.time_window_end,
        }

    @classmethod
    def from_dict(cls, d: dict) -> PullConfig:
        if not d:
            return cls()
        return cls(
            url=d.get("url", ""),
            method=d.get("method", "GET"),
            headers=d.get("headers"),
            body=d.get("body"),
            response_path=d.get("response_path", ""),
            field_map=d.get("field_map"),
            schedule_minutes=d.get("schedule_minutes", 1440),
            enabled=d.get("enabled", False),
            last_run=d.get("last_run"),
            last_status=d.get("last_status"),
            last_message=d.get("last_message"),
            last_rows=d.get("last_rows"),
            next_run=d.get("next_run"),
            time_window_start=d.get("time_window_start"),
            time_window_end=d.get("time_window_end"),
        )


class ExtConfig:
    """一个扩展数据源的完整配置。"""
    __slots__ = (
        "id", "label", "mode", "fields", "description",
        "symbol_map", "code_map",
        "created_at", "updated_at", "pull",
    )

    def __init__(
        self,
        id: str,
        label: str,
        mode: Literal["snapshot", "timeseries"],
        fields: list[ExtField],
        description: str = "",
        symbol_map: dict | None = None,
        code_map: dict | None = None,
        created_at: str | None = None,
        updated_at: str | None = None,
        pull: PullConfig | None = None,
    ) -> None:
        self.id = id
        self.label = label
        self.mode = mode
        self.fields = fields
        self.description = description
        # 映射关系: {"type": "mapped", "col": "原始列名"} 或 {"type": "computed", "from": "symbol|code", "method": "strip_exchange|append_exchange"}
        self.symbol_map = symbol_map or {}
        self.code_map = code_map or {}
        self.created_at = created_at or datetime.now().isoformat()
        self.updated_at = updated_at or datetime.now().isoformat()
        self.pull = pull

    def to_dict(self) -> dict:
        d = {
            "id": self.id,
            "label": self.label,
            "mode": self.mode,
            "fields": [f.to_dict() for f in self.fields],
            "description": self.description,
            "symbol_map": self.symbol_map,
            "code_map": self.code_map,
            "created_at": self.created_at,
            "updated_at": self.updated_at,
        }
        if self.pull:
            d["pull"] = self.pull.to_dict()
        return d

    @classmethod
    def from_dict(cls, d: dict) -> ExtConfig:
        return cls(
            id=d["id"],
            label=d["label"],
            mode=d["mode"],
            fields=[ExtField.from_dict(f) for f in d.get("fields", [])],
            description=d.get("description", ""),
            symbol_map=d.get("symbol_map"),
            code_map=d.get("code_map"),
            created_at=d.get("created_at"),
            updated_at=d.get("updated_at"),
            pull=PullConfig.from_dict(d["pull"]) if d.get("pull") else None,
        )


# ---------------------------------------------------------------------------
# 配置持久化
# ---------------------------------------------------------------------------

# load_all 进程内缓存: kline/screener/watchlist 等热路径每请求调用, 每次都
# iterdir + 逐 config.json read_text+parse 纯重复; 以配置目录的
# (目录名, mtime_ns, size) 签名失效 (新增/编辑/删除配置都会改变签名)。
_load_all_cache: dict[str, tuple[tuple, list[ExtConfig]]] = {}


def _ext_config_dir_signature(base: Path) -> tuple | None:
    """配置目录下所有 config.json 的 (目录名, mtime_ns, size) 签名; 出错返回 None (禁用缓存)。"""
    try:
        sig = []
        for d in sorted(base.iterdir()):
            cp = d / "config.json"
            if d.is_dir() and cp.exists():
                st = cp.stat()
                sig.append((d.name, st.st_mtime_ns, st.st_size))
        return tuple(sig)
    except Exception:  # noqa: BLE001
        return None


class ExtConfigStore:
    """扩展数据配置文件读写 — 每个表独立目录 data/ext/{config_id}/config.json。"""

    # 与创建端点 CreateExtReq.id 的 pattern 一致; load_all 之外的 config_id
    # 来自 URL path 参数, 必须先过白名单再拼路径, 防止 ../ 穿越删除。
    _VALID_ID = re.compile(r"^[a-zA-Z0-9_]+$")

    def __init__(self, data_dir: Path) -> None:
        self._base = data_dir / "ext_data"

    def _config_path(self, config_id: str) -> Path:
        if not self._VALID_ID.match(config_id):
            raise ValueError(f"非法 config_id: {config_id!r}")
        return self._base / config_id / "config.json"

    def load_all(self) -> list[ExtConfig]:
        # 兼容旧版: 如果目录为空且旧配置文件存在则迁移
        sig = _ext_config_dir_signature(self._base)
        if sig is not None:
            cached = _load_all_cache.get(str(self._base))
            if cached is not None and cached[0] == sig:
                return copy.deepcopy(cached[1])
        if not self._base.exists() or not any(self._base.iterdir()):
            old = self._base.parent / "ext_configs.json"
            if not old.exists():
                old = self._base.parent / "ext_configs.json.bak"
            if old.exists():
                self._migrate_legacy(old)
        if not self._base.exists():
            return []
        configs = []
        for d in sorted(self._base.iterdir()):
            cp = d / "config.json"
            if d.is_dir() and cp.exists():
                try:
                    raw = json.loads(cp.read_text(encoding="utf-8"))
                    configs.append(ExtConfig.from_dict(raw))
                except Exception as e:
                    logger.warning("扩展表配置解析失败 %s: %s", cp, e)
        if sig is not None and configs:
            # 缓存存私有副本, 命中时返回深拷贝, 调用方改配置对象不会污染缓存。
            _load_all_cache[str(self._base)] = (sig, copy.deepcopy(configs))
        return configs

    def get(self, config_id: str) -> ExtConfig | None:
        try:
            cp = self._config_path(config_id)
        except ValueError:
            return None
        if not cp.exists():
            return None
        try:
            raw = json.loads(cp.read_text(encoding="utf-8"))
            return ExtConfig.from_dict(raw)
        except Exception:
            return None

    def upsert(self, config: ExtConfig) -> None:
        config.updated_at = datetime.now().isoformat()
        cp = self._config_path(config.id)
        cp.parent.mkdir(parents=True, exist_ok=True)
        cp.write_text(
            json.dumps(config.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        # An edit that keeps the size can land within the filesystem's mtime resolution.
        _load_all_cache.pop(str(self._base), None)

    def delete(self, config_id: str) -> bool:
        import shutil
        try:
            cp = self._config_path(config_id)
        except ValueError:
            return False
        if not cp.exists():
            return False
        shutil.rmtree(cp.parent, ignore_errors=True)
        return True

    def _migrate_legacy(self, old_path: Path) -> None:
        """一次性迁移旧版 ext_configs.json 到独立目录结构。"""
        try:
            raw = json.loads(old_path.read_text(encoding="utf-8"))
            configs = [ExtConfig.from_dict(d) for d in raw]
            for c in configs:
                cp = self._config_path(c.id)
                cp.parent.mkdir(parents=True, exist_ok=True)
                cp.write_text(
                    json.dumps(c.to_dict(), ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
            # 迁移完成后重命名旧文件作为备份
            backup = old_path.with_suffix(".json.bak")
            old_path.rename(backup)
            logger.info("ext_configs.json 已迁移至 ext/ (备份: %s)", backup.name)
        except Exception as e:
            logger.warning("ext_configs 迁移失败: %s", e)


# ---------------------------------------------------------------------------
# CSV / Excel 解析 → Parquet 写入
# ---------------------------------------------------------------------------

_POLARS_DTYPE_MAP = {
    "string": pl.Utf8,
    "int": pl.Int64,
    "float": pl.Float64,
    "bool": pl.Boolean,
}


def build_code_lookup(data_dir: Path) -> dict[str, str]:
    """从 instruments 维表构建 code → symbol 映射。"""
    path = data_dir / "instruments" / "instruments.parquet"
    if not path.exists():
        return {}
    try:
        df = pl.read_parquet(path, columns=["code", "symbol"])
        return dict(zip(df["code"].to_list(), df["symbol"].to_list()))
    except Exception:
        return {}


def build_taiwan_code_lookup() -> dict[str, str]:
    """Build a bare-code → canonical-symbol map from the Taiwan security master.

    A code listed on both exchanges is dropped: a bare code carries nothing that
    would pick one of them, and guessing would silently mis-file the row.
    """
    from app.taiwan.symbol import Exchange, parse_symbol
    from app.taiwan.universe import get_security_master

    try:
        df = get_security_master().to_dataframe()
    except Exception as e:
        logger.warning("Taiwan security master unavailable, symbols keep bare codes: %s", e)
        return {}
    if df.is_empty() or "code" not in df.columns or "exchange" not in df.columns:
        return {}

    resolved: dict[str, str] = {}
    ambiguous: set[str] = set()
    for raw_code, raw_exchange in zip(df["code"].to_list(), df["exchange"].to_list(), strict=True):
        code = str(raw_code or "").strip()
        if not code or code in ambiguous:
            continue
        try:
            canonical = parse_symbol(code, exchange=Exchange(str(raw_exchange))).canonical
        except ValueError:
            continue
        if resolved.setdefault(code, canonical) != canonical:
            del resolved[code]
            ambiguous.add(code)
    return resolved


def normalize_symbol(series: pl.Series, lookup: dict[str, str] | None = None) -> pl.Series:
    """Normalise a symbol column to ``{code}.{EXCHANGE}``.

    A bare code is resolved against ``lookup`` (the instruments dimension table)
    first, then against the Taiwan security master. A code neither of them knows
    is returned unchanged rather than given a guessed exchange suffix.
    """
    _lookup = lookup or {}
    taiwan_lookup: dict[str, str] | None = None

    def _fix_one(val: str) -> str:
        nonlocal taiwan_lookup
        if not val:
            return val
        val = val.strip()
        if "." in val:
            return val
        mapped = _lookup.get(val)
        if mapped:
            return mapped
        # Built on first miss only: an all-canonical column must not pay for it,
        # because the security master fetches from TWSE/TPEx when it has no
        # local cache (TaiwanSecurityMaster.ensure_loaded).
        if taiwan_lookup is None:
            taiwan_lookup = build_taiwan_code_lookup()
        return taiwan_lookup.get(val, val)

    return series.map_elements(_fix_one, return_dtype=pl.Utf8)


def apply_config_mapping(df: pl.DataFrame, config: ExtConfig, data_dir: Path) -> pl.DataFrame:
    """根据 config 的 symbol_map / code_map 自动生成 symbol 和 code 列。"""
    sm = config.symbol_map or {}
    cm = config.code_map or {}

    if sm.get("type") == "mapped" and sm["col"] in df.columns:
        df = df.with_columns(df[sm["col"]].cast(pl.Utf8).alias("symbol"))

    if cm.get("type") == "mapped" and cm["col"] in df.columns:
        df = df.with_columns(df[cm["col"]].cast(pl.Utf8).alias("code"))

    if "symbol" not in df.columns and sm.get("type") == "computed":
        if sm.get("from") == "code" and "code" in df.columns:
            lookup = build_code_lookup(data_dir)
            df = df.with_columns(normalize_symbol(df["code"].cast(pl.Utf8), lookup).alias("symbol"))

    if "code" not in df.columns and cm.get("type") == "computed":
        if cm.get("from") == "symbol" and "symbol" in df.columns:
            df = df.with_columns(
                df["symbol"].cast(pl.Utf8).str.split(".").list.first().alias("code")
            )

    if "symbol" in df.columns and "code" not in df.columns:
        df = df.with_columns(
            df["symbol"].cast(pl.Utf8).str.split(".").list.first().alias("code")
        )
    elif "code" in df.columns and "symbol" not in df.columns:
        lookup = build_code_lookup(data_dir)
        df = df.with_columns(normalize_symbol(df["code"].cast(pl.Utf8), lookup).alias("symbol"))

    if "symbol" in df.columns:
        lookup = build_code_lookup(data_dir)
        df = df.with_columns(normalize_symbol(df["symbol"].cast(pl.Utf8), lookup))

    return df


def cast_df_to_schema(df: pl.DataFrame, fields: list[ExtField]) -> pl.DataFrame:
    """按配置的字段类型转换 DataFrame 列类型。

    List → string 的处理: 上游接口常返回数组字段 (如 concepts: ["AI", "芯片"]),
    若声明为 string, 直接 cast 会抛 `cannot cast List type`。
    这里把列表元素先转字符串再以分号拼接, 与 _flatten_concept_rows 保持一致。
    """
    schema = df.schema
    for f in fields:
        if f.name not in df.columns:
            continue
        target = _POLARS_DTYPE_MAP.get(f.dtype, pl.Utf8)
        src = schema[f.name]
        if isinstance(src, pl.List) and target == pl.Utf8:
            df = df.with_columns(
                pl.col(f.name).cast(pl.List(pl.Utf8)).list.join(";").cast(target)
            )
        else:
            df = df.with_columns(pl.col(f.name).cast(target))
    return df


def _config_dir(config_id: str, data_dir: Path) -> Path:
    """返回扩展配置的根目录 data/ext_data/{config_id}/。"""
    return data_dir / "ext_data" / config_id


def write_ext_parquet(
    df: pl.DataFrame,
    config: ExtConfig,
    data_dir: Path,
    snapshot_date: date | None = None,
) -> int:
    """将 DataFrame 写入扩展数据 Parquet。

    目录结构:
      - snapshot:  data/ext_data/{id}/part.parquet（与 config.json 同级，覆盖写）
      - timeseries: data/ext_data/{id}/timeseries/date=xxx/part.parquet（按日分区）

    Returns:
        写入行数。
    """
    snap = snapshot_date or date.today()
    cfg_dir = _config_dir(config.id, data_dir)

    # 标准化 symbol 列: 用维表查找 → 准确匹配交易所
    if "symbol" in df.columns:
        lookup = build_code_lookup(data_dir)
        df = df.with_columns(normalize_symbol(df["symbol"], lookup))

    if config.mode == "snapshot":
        # 快照: 与 config.json 同级，直接覆盖
        cfg_dir.mkdir(parents=True, exist_ok=True)
        out_path = cfg_dir / "part.parquet"

        # 如果已有文件，合并去重后覆盖
        if out_path.exists():
            try:
                existing = pl.read_parquet(out_path)
                key = "symbol" if "symbol" in df.columns else df.columns[0]
                df = pl.concat([existing, df]).unique(subset=[key], keep="last")
            except Exception as e:
                # schema 不一致 (列不同) 时 concat 失败 → 直接用新 df 覆盖。
                # 记日志而非静默吞掉, 便于排查"数据结构错乱"类问题。
                logger.warning("扩展表 %s 合并去重失败, 将覆盖写入: %s", config.id, e)
    else:
        # 时序: timeseries/ 下按日期分区
        out_dir = cfg_dir / "timeseries" / f"date={snap}"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "part.parquet"

        # 如果已有文件，合并去重
        if out_path.exists():
            try:
                existing = pl.read_parquet(out_path)
                key = "symbol" if "symbol" in df.columns else df.columns[0]
                df = pl.concat([existing, df]).unique(subset=[key], keep="last")
            except Exception as e:
                logger.warning("扩展表 %s 合并去重失败, 将覆盖写入: %s", config.id, e)

    df = cast_df_to_schema(df, config.fields)
    df.write_parquet(out_path)
    logger.info("扩展表写入: %s → %s (%d 行)", config.id, out_path, len(df))
    return len(df)


def rows_to_parquet(
    rows: list[dict],
    config: ExtConfig,
    data_dir: Path,
    snapshot_date: date | None = None,
) -> int:
    """将 JSON 行列表转为 DataFrame 写入 Parquet，复用 write_ext_parquet 的存储逻辑。

    Returns:
        写入行数。
    """
    df = pl.DataFrame(rows)
    df = apply_config_mapping(df, config, data_dir)
    if "symbol" in df.columns:
        df = df.with_columns(pl.col("symbol").cast(pl.Utf8))
    return write_ext_parquet(df, config, data_dir, snapshot_date=snapshot_date)
