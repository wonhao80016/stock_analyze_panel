"""Taiwan Security Master Service & Universe Orchestrator.

Responsibilities:
  - Orchestrates TWSE & TPEx adapters to build unified securities master
  - Deduplication and conflict resolution (canonical symbol key)
  - Parquet persistence & local disk caching
  - Dynamic universe generation (TAIWAN_ALL, TWSE_ALL, TPEX_ALL, STOCKS, ETFS)
  - Multi-attribute instrument search (code, symbol, Traditional Chinese name)
  - Standardized Polars DataFrame export for MarketDataProvider contract
"""
from __future__ import annotations

import contextlib
import json
import logging
import os
import threading
import time
import uuid
from collections.abc import Callable, Iterable, Iterator
from dataclasses import dataclass, field, replace
from datetime import datetime
from html import unescape
from pathlib import Path
from typing import Any, BinaryIO

import polars as pl

from app.taiwan.universe.adapters import (
    TpexInstrumentAdapter,
    TwseInstrumentAdapter,
    normalize_listing_date,
)
from app.taiwan.universe.industry_classification import resolve_industry_name
from app.taiwan.universe.models import (
    MarketProfileBridge,
    TaiwanInstrument,
    UniverseType,
    derive_security_class,
    is_tdr_industry,
)

logger = logging.getLogger(__name__)

_REPLACE_ATTEMPTS = 5
_REPLACE_RETRY_SECONDS = 0.2
MIN_REFRESH_COUNT_RATIO = 0.9
MAX_REMOVALS_PER_EXCHANGE = 20
MAX_REMOVAL_RATIO = 0.02
_FIRST_LOAD_WAIT_SECONDS = 60.0
_FIRST_LOAD_POLL_SECONDS = 0.5
_EXCHANGES = ("TWSE", "TPEX")
_SUPPORTED_TYPES = ("stock", "etf")
_CLASSIFICATION_FIELDS = (
    "instrument_type",
    "is_supported",
    "etf_category",
    "classification_source",
    "underlying_scope",
    "leverage_multiplier",
    "etf_management_style",
    "security_class",
    "currency",
    "lot_size",
)
_FRAME_SCHEMA = {
    "symbol": pl.String,
    "code": pl.String,
    "exchange": pl.String,
    "name": pl.String,
    "instrument_type": pl.String,
    "listing_status": pl.String,
    "listing_date": pl.String,
    "isin": pl.String,
    "industry": pl.String,
    "cfi_code": pl.String,
    "raw_category": pl.String,
    "is_supported": pl.Boolean,
    "source": pl.String,
    "updated_at": pl.String,
    "etf_category": pl.String,
    "classification_source": pl.String,
    "underlying_scope": pl.String,
    "leverage_multiplier": pl.Float64,
    "etf_management_style": pl.String,
    "currency": pl.String,
    "lot_size": pl.Int64,
    "security_class": pl.String,
    "delisted_at": pl.String,
}


class SecurityMasterUnavailableError(RuntimeError):
    """The security master cannot be loaded: its cache is unreadable or the first download failed."""


class SecurityMasterValidationError(ValueError):
    """A freshly fetched instrument list failed the checks that guard the cached master."""


@dataclass(frozen=True)
class SecurityMasterRefreshResult:
    """Outcome of TaiwanSecurityMaster.refresh; *changed* maps symbol -> field -> [old, new].

    *busy* means another refresh held the lock, so nothing was fetched or written. *styles_complete*
    is False when TPEx management style lookups were skipped or stopped before every unknown code.
    """

    ok: bool
    dry_run: bool
    attempted_at: str
    error: str | None = None
    busy: bool = False
    counts: dict[str, dict[str, int]] = field(default_factory=dict)
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    changed: dict[str, dict[str, list[Any]]] = field(default_factory=dict)
    management_style_lookups: dict[str, int] = field(default_factory=dict)
    styles_complete: bool = True


def write_atomically(target: Path, write: Callable[[Path], None]) -> None:
    """Write *target* through a sibling temp file and os.replace, so no reader sees a partial file.

    Windows refuses the replace while another process holds the target open, so it is retried briefly.
    """
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{uuid.uuid4().hex}.tmp")
    try:
        write(tmp)
        for attempt in range(1, _REPLACE_ATTEMPTS + 1):
            try:
                os.replace(tmp, target)
                return
            except PermissionError:
                if attempt == _REPLACE_ATTEMPTS:
                    raise
                logger.info("Replacing %s is blocked by a reader; retrying", target)
                time.sleep(_REPLACE_RETRY_SECONDS * attempt)
    finally:
        with contextlib.suppress(OSError):
            tmp.unlink(missing_ok=True)


def _lock_first_byte(stream: BinaryIO, *, unlock: bool) -> None:
    if os.name == "nt":
        import msvcrt

        stream.seek(0)
        msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK if unlock else msvcrt.LK_NBLCK, 1)
        return

    import fcntl

    fcntl.flock(stream.fileno(), fcntl.LOCK_UN if unlock else fcntl.LOCK_EX | fcntl.LOCK_NB)


@contextlib.contextmanager
def _refresh_file_lock(path: Path) -> Iterator[bool]:
    """Yield whether this handle holds the cross-process refresh lock on *path*.

    The OS drops the byte-range lock when its holder exits, so a crashed refresh leaves no stale lock.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as stream:
        stream.seek(0, os.SEEK_END)
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        try:
            _lock_first_byte(stream, unlock=False)
        except OSError:
            yield False
            return
        try:
            yield True
        finally:
            with contextlib.suppress(OSError):
                _lock_first_byte(stream, unlock=True)


def _file_signature(path: Path) -> tuple[int, int] | None:
    try:
        stat = path.stat()
    except OSError:
        return None
    return stat.st_mtime_ns, stat.st_size


def _instruments_frame(
    instruments: Iterable[TaiwanInstrument], delisted_at: dict[str, str] | None = None
) -> pl.DataFrame:
    delisted_at = delisted_at or {}
    rows = [{**inst.to_dict(), "delisted_at": delisted_at.get(inst.symbol)} for inst in instruments]
    if not rows:
        return pl.DataFrame(schema=_FRAME_SCHEMA)
    return pl.DataFrame(rows, schema=_FRAME_SCHEMA)


def _read_instruments(path: Path) -> tuple[dict[str, TaiwanInstrument], dict[str, str]]:
    """Instruments in the cache file and the ISO date each delisted one was first found missing."""
    loaded: dict[str, TaiwanInstrument] = {}
    delisted_at: dict[str, str] = {}
    for r in pl.read_parquet(path).to_dicts():
        raw_lot_size = r.get("lot_size")
        name = unescape(r["name"])
        raw_industry = r.get("industry")
        security_class = r.get("security_class") or derive_security_class(
            r["code"], name, raw_industry
        )
        inst = TaiwanInstrument(
            symbol=r["symbol"],
            code=r["code"],
            exchange=r["exchange"],
            # Older official JSON caches retain HTML character references.
            # Normalize in memory without rewriting the user's cache.
            name=name,
            instrument_type=r["instrument_type"],
            listing_status=r["listing_status"],
            listing_date=normalize_listing_date(r.get("listing_date")),
            isin=r.get("isin"),
            # Older caches may hold raw codes ("24"); 91 is the TDR marker, not an industry.
            industry=(
                None
                if is_tdr_industry(raw_industry)
                else resolve_industry_name(raw_industry)
            ),
            cfi_code=r.get("cfi_code"),
            raw_category=r.get("raw_category", ""),
            is_supported=bool(r.get("is_supported", False)),
            source=r.get("source", "cache"),
            updated_at=r.get("updated_at", ""),
            etf_category=r.get("etf_category"),
            classification_source=r.get("classification_source"),
            underlying_scope=r.get("underlying_scope"),
            leverage_multiplier=float(r.get("leverage_multiplier") or 1.0),
            etf_management_style=r.get("etf_management_style"),
            currency=r.get("currency") or "TWD",
            lot_size=1000 if raw_lot_size is None else int(raw_lot_size),
            security_class=security_class,
        )
        loaded[inst.symbol] = inst
        if inst.listing_status == "delisted" and r.get("delisted_at"):
            delisted_at[inst.symbol] = str(r["delisted_at"])
    return loaded, delisted_at


def _taipei_timestamp(now: datetime | None) -> str:
    from app.taiwan.realtime.calendar import TAIPEI_TZ

    moment = now or datetime.now(TAIPEI_TZ)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=TAIPEI_TZ)
    return moment.astimezone(TAIPEI_TZ).isoformat(timespec="seconds")


def _is_active_product(inst: TaiwanInstrument) -> bool:
    return (
        inst.listing_status == "active"
        and inst.is_supported
        and inst.exchange in _EXCHANGES
        and inst.instrument_type in _SUPPORTED_TYPES
    )


def _active_counts(instruments: dict[str, TaiwanInstrument]) -> dict[str, dict[str, int]]:
    counts = {exchange: dict.fromkeys(_SUPPORTED_TYPES, 0) for exchange in _EXCHANGES}
    for inst in instruments.values():
        if _is_active_product(inst):
            counts[inst.exchange][inst.instrument_type] += 1
    return counts


def _validate_refresh(
    fresh: dict[str, TaiwanInstrument], previous: dict[str, TaiwanInstrument], *, force: bool
) -> None:
    """Reject a fetched list that looks broken; *force* accepts large count drops and removals.

    Both company directories must be non-empty; without a previous master the count comparisons
    are skipped.
    """
    for inst in fresh.values():
        if (
            inst.exchange not in _EXCHANGES
            or not inst.code
            or not inst.name
            or inst.symbol != f"{inst.code}.{inst.exchange}"
            or inst.instrument_type not in (*_SUPPORTED_TYPES, "unsupported")
            or inst.listing_status not in ("active", "unsupported")
        ):
            raise SecurityMasterValidationError(
                f"unexpected official instrument record {inst.symbol!r}"
            )
    fresh_counts, previous_counts = _active_counts(fresh), _active_counts(previous)
    for exchange in _EXCHANGES:
        if fresh_counts[exchange]["stock"] == 0:
            raise SecurityMasterValidationError(f"{exchange} company directory is empty")
    if not previous or force:
        return
    for exchange in _EXCHANGES:
        for kind in _SUPPORTED_TYPES:
            before, after = previous_counts[exchange][kind], fresh_counts[exchange][kind]
            if after < MIN_REFRESH_COUNT_RATIO * before:
                raise SecurityMasterValidationError(
                    f"{exchange} {kind} count dropped from {before} to {after}; "
                    "refresh with force=True to accept"
                )
        removed = sum(
            1
            for symbol, inst in previous.items()
            if inst.exchange == exchange and _is_active_product(inst) and symbol not in fresh
        )
        cap = max(
            MAX_REMOVALS_PER_EXCHANGE,
            int(MAX_REMOVAL_RATIO * sum(previous_counts[exchange].values())),
        )
        if removed > cap:
            raise SecurityMasterValidationError(
                f"{exchange} would delist {removed} symbols, more than the {cap} allowed; "
                "refresh with force=True to accept"
            )


def _merge_refresh(
    fresh: dict[str, TaiwanInstrument],
    previous: dict[str, TaiwanInstrument],
    previous_delisted_at: dict[str, str],
    stamp: str,
) -> tuple[dict[str, TaiwanInstrument], dict[str, str], list[str], list[str]]:
    """Fresh instruments plus the previous ones they lack, every row stamped with *stamp*.

    A previously active symbol missing from the official list stays queryable as delisted, dated
    with the refresh day; a symbol that stays missing keeps its first delisted date.
    """
    merged = dict(fresh)
    delisted_at: dict[str, str] = {}
    removed: list[str] = []
    for symbol, old in previous.items():
        if symbol in merged:
            continue
        if old.listing_status == "active":
            old = replace(old, listing_status="delisted")
            delisted_at[symbol] = stamp[:10]
            removed.append(symbol)
        elif old.listing_status == "delisted" and symbol in previous_delisted_at:
            delisted_at[symbol] = previous_delisted_at[symbol]
        merged[symbol] = old
    added = [
        symbol
        for symbol, inst in fresh.items()
        if _is_active_product(inst)
        and (symbol not in previous or previous[symbol].listing_status != "active")
    ]
    stamped = {symbol: replace(inst, updated_at=stamp) for symbol, inst in merged.items()}
    return stamped, delisted_at, sorted(added), sorted(removed)


def _rule_profile(inst: TaiwanInstrument) -> dict[str, Any]:
    try:
        limit: Any = MarketProfileBridge.get_price_limit_pct(inst)
    except ValueError:
        limit = "unknown"
    try:
        tax: Any = MarketProfileBridge.get_tax_class(inst).value
    except ValueError:
        tax = "unknown"
    return {"price_limit_pct": limit, "tax_class": tax}


def _classification_changes(
    previous: dict[str, TaiwanInstrument], merged: dict[str, TaiwanInstrument]
) -> dict[str, dict[str, list[Any]]]:
    changes: dict[str, dict[str, list[Any]]] = {}
    for symbol in sorted(merged):
        old, new = previous.get(symbol), merged[symbol]
        if old is None:
            continue
        diff = {
            name: [getattr(old, name), getattr(new, name)]
            for name in _CLASSIFICATION_FIELDS
            if getattr(old, name) != getattr(new, name)
        }
        old_rules, new_rules = _rule_profile(old), _rule_profile(new)
        for name, old_value in old_rules.items():
            if old_value != new_rules[name]:
                diff[name] = [old_value, new_rules[name]]
        if diff:
            changes[symbol] = diff
    return changes


def _tpex_detail_type(inst: TaiwanInstrument) -> str:
    if inst.etf_category == "bond":
        return "bond"
    return "domestic" if inst.underlying_scope == "domestic" else "foreign"


class TaiwanSecurityMaster:
    """Orchestrates Taiwan instruments master and universes."""

    def __init__(
        self,
        cache_path: Path | None = None,
        twse_adapter: TwseInstrumentAdapter | None = None,
        tpex_adapter: TpexInstrumentAdapter | None = None,
    ) -> None:
        # Phase 8B-5.0.6: 省略 cache_path 时锚定到 settings.data_dir, 不再是
        # cwd-dependent 的裸相对路径(见 app/taiwan/data_root.py)。显式传入
        # cache_path(既有 deterministic test 都这样做)时行为不变。
        if cache_path is None:
            from app.taiwan.data_root import taiwan_data_root

            cache_path = taiwan_data_root() / "security_master.parquet"
        self.cache_path = Path(cache_path)
        self.twse_adapter = twse_adapter or TwseInstrumentAdapter()
        self.tpex_adapter = tpex_adapter or TpexInstrumentAdapter()
        self._instruments: dict[str, TaiwanInstrument] = {}
        self._delisted_at: dict[str, str] = {}
        self._loaded = False
        # (mtime_ns, size) of the cache file when the current instruments were installed.
        self._loaded_signature: tuple[int, int] | None = None
        self._refresh_lock = threading.Lock()
        self._first_load_lock = threading.Lock()

    @property
    def refresh_metadata_path(self) -> Path:
        return self.cache_path.with_suffix(".refresh.json")

    @property
    def refresh_lock_path(self) -> Path:
        return self.cache_path.with_suffix(".refresh.lock")

    @contextlib.contextmanager
    def refresh_lock(self) -> Iterator[bool]:
        """Yield whether the cross-process refresh lock is held; False while a refresh holds it."""
        with _refresh_file_lock(self.refresh_lock_path) as held:
            yield held

    def _fetch_from_adapters(
        self,
        twse_html: str | None = None,
        tpex_html: str | None = None,
    ) -> dict[str, TaiwanInstrument]:
        twse_items = self.twse_adapter.get_instruments(twse_html)
        tpex_items = self.tpex_adapter.get_instruments(tpex_html)

        merged: dict[str, TaiwanInstrument] = {}

        # 1. Add TWSE items
        for item in twse_items:
            merged[item.symbol] = item

        # 2. Add TPEx items (detect conflicts)
        for item in tpex_items:
            if item.symbol in merged:
                logger.warning("Duplicate canonical symbol conflict: %s. Keeping TWSE entry.", item.symbol)
                continue
            merged[item.symbol] = item
        return merged

    def load_from_adapters(
        self,
        twse_html: str | None = None,
        tpex_html: str | None = None,
    ) -> int:
        """Fetch and merge instruments directly from TWSE and TPEx adapters."""
        merged = self._fetch_from_adapters(twse_html, tpex_html)
        self._replace_instruments(merged)
        logger.info("Loaded %d Taiwan instruments from official adapters.", len(merged))
        return len(merged)

    def _replace_instruments(
        self,
        instruments: dict[str, TaiwanInstrument],
        *,
        delisted_at: dict[str, str] | None = None,
        cache_signature: tuple[int, int] | None = None,
    ) -> None:
        """Install a new instrument set and drop the limit profiles cached from the previous one.

        *cache_signature* is the cache file state the set corresponds to; by default the current one,
        so only a later change of the file makes ensure_loaded() reload it.
        """
        from app.indicators.pipeline import invalidate_taiwan_limit_profile_cache

        self._instruments = instruments
        self._delisted_at = dict(delisted_at or {})
        self._loaded = True
        self._loaded_signature = cache_signature or _file_signature(self.cache_path)
        invalidate_taiwan_limit_profile_cache()

    def save_cache(self, path: Path | None = None) -> Path:
        """Persist current instruments to Parquet, replacing the file atomically."""
        target = Path(path) if path else self.cache_path
        df = self.to_dataframe()
        write_atomically(target, df.write_parquet)
        if target == self.cache_path:
            self._loaded_signature = _file_signature(target)
        logger.info("Persisted %d Taiwan instruments to %s", len(df), target)
        return target

    def load_cache(self, path: Path | None = None) -> bool:
        """Load instruments from Parquet disk cache if present."""
        target = Path(path) if path else self.cache_path
        if not target.exists():
            return False
        signature = _file_signature(target)
        try:
            loaded, delisted_at = _read_instruments(target)
        except Exception as e:
            logger.warning("Failed to load instruments cache %s: %s", target, e)
            return False
        if target == self.cache_path:
            self._replace_instruments(loaded, delisted_at=delisted_at, cache_signature=signature)
        else:
            self._replace_instruments(loaded, delisted_at=delisted_at)
        logger.info("Loaded %d instruments from cache %s", len(loaded), target)
        return True

    def ensure_loaded(self) -> None:
        """Load the master once, and reload it whenever the cache file changes on disk.

        An unreadable cache file is never replaced by a download: loaded instruments stay, and with
        none loaded SecurityMasterUnavailableError is raised. Only a missing cache is downloaded,
        through refresh() without the slow TPEx management style lookups; while another refresh holds
        the lock, this waits up to _FIRST_LOAD_WAIT_SECONDS for its cache file.
        """
        if self._loaded and self._instruments:
            self.reload_if_changed()
            return
        with self._first_load_lock:
            if self._loaded and self._instruments:
                return
            if self.load_cache():
                return
            if self.cache_path.exists():
                raise SecurityMasterUnavailableError(
                    f"Taiwan security master cache {self.cache_path.name} is unreadable; "
                    "refusing to replace it with a download"
                )
            result = self.refresh(resolve_management_styles=False)
            deadline = time.monotonic() + _FIRST_LOAD_WAIT_SECONDS
            while result.busy:
                if time.monotonic() >= deadline:
                    raise SecurityMasterUnavailableError(
                        f"Taiwan security master download failed: {result.error}"
                    )
                time.sleep(_FIRST_LOAD_POLL_SECONDS)
                if self.cache_path.exists():
                    if self.load_cache():
                        return
                    raise SecurityMasterUnavailableError(
                        f"Taiwan security master cache {self.cache_path.name} is unreadable"
                    )
                result = self.refresh(resolve_management_styles=False)
            if not result.ok:
                raise SecurityMasterUnavailableError(
                    f"Taiwan security master download failed: {result.error}"
                )

    def reload_if_changed(self) -> None:
        """Reload the cache file if it changed on disk since the loaded instruments were installed.

        Does nothing before the first load, so it never downloads; an unreadable changed file keeps
        the loaded instruments.
        """
        if not (self._loaded and self._instruments):
            return
        signature = _file_signature(self.cache_path)
        if signature is None or signature == self._loaded_signature:
            return
        if not self.load_cache():
            logger.warning(
                "Keeping the loaded security master; the changed cache %s is unreadable",
                self.cache_path,
            )
            self._loaded_signature = signature

    def refresh(
        self,
        *,
        now: datetime | None = None,
        dry_run: bool = False,
        force: bool = False,
        resolve_management_styles: bool = True,
    ) -> SecurityMasterRefreshResult:
        """Rebuild the master from the official directories; replace the cache only if the result validates.

        Fetch, validation and write failures are returned, not raised: the cache file and the loaded
        instruments stay unchanged. Unless *dry_run*, the outcome is recorded in refresh_metadata_path;
        a dry run reads the official sources but writes and installs nothing. *force* accepts count
        drops and removals beyond the validation limits. A refresh already running in this or another
        process makes this one return busy without fetching; an unusable lock file fails it.
        """
        stamp = _taipei_timestamp(now)
        if not self._refresh_lock.acquire(blocking=False):
            return self._busy_result(stamp, dry_run)
        try:
            if dry_run:
                return self._refresh_locked(stamp, True, force, resolve_management_styles)
            with _refresh_file_lock(self.refresh_lock_path) as held:
                if not held:
                    return self._busy_result(stamp, dry_run)
                result = self._refresh_locked(stamp, False, force, resolve_management_styles)
                self._record_refresh(result)
                return result
        except OSError as exc:
            error = (
                f"cannot use the refresh lock {self.refresh_lock_path.name}: {exc.strerror or exc}"
            )
            logger.warning("Taiwan security master refresh failed: %s", error)
            return SecurityMasterRefreshResult(
                ok=False, dry_run=dry_run, attempted_at=stamp, error=error
            )
        finally:
            self._refresh_lock.release()

    @staticmethod
    def _busy_result(stamp: str, dry_run: bool) -> SecurityMasterRefreshResult:
        logger.info("Skipping Taiwan security master refresh; another refresh is in progress")
        return SecurityMasterRefreshResult(
            ok=False,
            dry_run=dry_run,
            attempted_at=stamp,
            error="another Taiwan security master refresh is in progress",
            busy=True,
        )

    def _refresh_locked(
        self, stamp: str, dry_run: bool, force: bool, resolve_management_styles: bool
    ) -> SecurityMasterRefreshResult:
        try:
            previous, previous_delisted_at = self._refresh_baseline()
            fresh = self._fetch_from_adapters()
            _validate_refresh(fresh, previous, force=force)
            lookups, styles_complete = (
                self._resolve_tpex_management_styles(fresh, previous)
                if resolve_management_styles
                else ({"queried": 0, "resolved": 0}, False)
            )
            merged, delisted_at, added, removed = _merge_refresh(
                fresh, previous, previous_delisted_at, stamp
            )
            result = SecurityMasterRefreshResult(
                ok=True,
                dry_run=dry_run,
                attempted_at=stamp,
                counts=_active_counts(merged),
                added=added,
                removed=removed,
                changed=_classification_changes(previous, merged),
                management_style_lookups=lookups,
                styles_complete=styles_complete,
            )
            if not dry_run:
                frame = _instruments_frame(merged.values(), delisted_at)
                write_atomically(self.cache_path, frame.write_parquet)
                self._replace_instruments(merged, delisted_at=delisted_at)
        except Exception as exc:
            error = (
                str(exc)
                if isinstance(exc, SecurityMasterValidationError)
                else f"{type(exc).__name__}: {exc}"
            )
            logger.warning(
                "Taiwan security master refresh failed, keeping the current master: %s", error
            )
            return SecurityMasterRefreshResult(
                ok=False, dry_run=dry_run, attempted_at=stamp, error=error
            )
        logger.info(
            "Taiwan security master %s: %d added, %d delisted, %d reclassified",
            "dry run" if dry_run else "refreshed",
            len(result.added),
            len(result.removed),
            len(result.changed),
        )
        return result

    def _refresh_baseline(self) -> tuple[dict[str, TaiwanInstrument], dict[str, str]]:
        loaded = (dict(self._instruments), dict(self._delisted_at)) if self._loaded else ({}, {})
        if not self.cache_path.exists():
            return loaded
        try:
            return _read_instruments(self.cache_path)
        except Exception as exc:
            logger.warning(
                "Security master cache %s is unreadable; refreshing against the %s: %s",
                self.cache_path,
                "loaded master" if loaded[0] else "empty baseline",
                exc,
            )
            return loaded

    def _resolve_tpex_management_styles(
        self, fresh: dict[str, TaiwanInstrument], previous: dict[str, TaiwanInstrument]
    ) -> tuple[dict[str, int], bool]:
        """Fill unknown TPEx ETF management styles in *fresh*: carried forward, else looked up officially.

        The TPEx ISIN directory has no active/passive marker, which decides the bond ETF tax exemption.
        Styles resolved before a lookup failure are kept. Also returns whether every unknown code
        got an answer.
        """
        unknown: dict[str, str] = {}
        for symbol, inst in fresh.items():
            if (
                inst.exchange != "TPEX"
                or inst.instrument_type != "etf"
                or inst.etf_management_style is not None
            ):
                continue
            known = previous.get(symbol)
            if known is not None and known.etf_management_style is not None:
                fresh[symbol] = replace(inst, etf_management_style=known.etf_management_style)
            else:
                unknown[inst.code] = symbol
        lookup = getattr(self.tpex_adapter, "iter_etf_management_styles", None)
        if not unknown or lookup is None:
            return {"queried": 0, "resolved": 0}, not unknown
        detail_types = {code: _tpex_detail_type(fresh[symbol]) for code, symbol in unknown.items()}
        resolved = 0
        answered: set[str] = set()
        try:
            for code, style in lookup(detail_types):
                answered.add(code)
                if style is not None and code in unknown:
                    fresh[unknown[code]] = replace(fresh[unknown[code]], etf_management_style=style)
                    resolved += 1
        except Exception as exc:
            logger.warning("TPEx ETF management style lookup stopped: %s", exc)
        return {"queried": len(unknown), "resolved": resolved}, set(unknown) <= answered

    def _record_refresh(self, result: SecurityMasterRefreshResult) -> None:
        path = self.refresh_metadata_path
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            record = {}
        if not isinstance(record, dict):
            record = {}
        record.setdefault("last_success", None)
        record["last_attempt"] = result.attempted_at
        record["error"] = result.error
        if result.ok:
            record.update(
                last_success=result.attempted_at,
                counts=result.counts,
                added=result.added,
                removed=result.removed,
                reclassified=len(result.changed),
                management_style_lookups=result.management_style_lookups,
                styles_complete=result.styles_complete,
            )
        text = json.dumps(record, ensure_ascii=False, indent=2)
        try:
            write_atomically(path, lambda tmp: tmp.write_text(text, encoding="utf-8"))
        except OSError as exc:
            logger.warning("Could not record the security master refresh in %s: %s", path, exc)

    def get_instrument(self, symbol: str) -> TaiwanInstrument | None:
        """Get canonical instrument metadata by symbol (e.g. '2330.TWSE')."""
        self.ensure_loaded()
        return self._instruments.get(symbol)

    def get_universe(self, universe_type: UniverseType) -> list[str]:
        """Generate list of canonical symbols for a specified universe."""
        self.ensure_loaded()
        symbols: list[str] = []
        for inst in self._instruments.values():
            if not inst.is_supported or inst.listing_status != "active":
                continue
            if universe_type == UniverseType.TAIWAN_ALL:
                symbols.append(inst.symbol)
            elif universe_type == UniverseType.TWSE_ALL and inst.exchange == "TWSE":
                symbols.append(inst.symbol)
            elif universe_type == UniverseType.TPEX_ALL and inst.exchange == "TPEX":
                symbols.append(inst.symbol)
            elif universe_type == UniverseType.TAIWAN_STOCKS and inst.instrument_type == "stock":
                symbols.append(inst.symbol)
            elif universe_type == UniverseType.TAIWAN_ETFS and inst.instrument_type == "etf":
                symbols.append(inst.symbol)
        return sorted(symbols)

    def search(self, q: str, limit: int = 20) -> list[dict[str, Any]]:
        """Search instruments by code, canonical symbol, or Traditional Chinese name.

        Prioritizes supported stocks/ETFs and exact/prefix matches over warrants and partial matches.
        """
        self.ensure_loaded()
        query = (q or "").strip().upper()
        if not query:
            return []

        scored_candidates: list[tuple[int, TaiwanInstrument]] = []
        for inst in self._instruments.values():
            code_upper = inst.code.upper()
            name_upper = inst.name.upper()
            sym_upper = inst.symbol.upper()

            score = 0
            if sym_upper == query:
                score = 100
            elif code_upper == query:
                score = 90
            elif name_upper == query:
                score = 85
            elif code_upper.startswith(query):
                score = 70
            elif name_upper.startswith(query):
                score = 60
            elif query in sym_upper:
                score = 50
            elif query in name_upper:
                score = 40
            else:
                continue

            # Prioritize supported securities (stocks/ETFs) over warrants/unsupported
            if inst.is_supported:
                score += 200

            scored_candidates.append((score, inst))

        # Sort descending by score
        scored_candidates.sort(key=lambda x: x[0], reverse=True)

        results: list[dict[str, Any]] = []
        for _, inst in scored_candidates[:limit]:
            results.append({
                "symbol": inst.symbol,
                "code": inst.code,
                "name": inst.name,
                "exchange": inst.exchange,
                "instrument_type": inst.instrument_type,
                "is_supported": inst.is_supported,
            })
        return results


    def to_dataframe(self, supported_only: bool = False) -> pl.DataFrame:
        """Export instruments as a Polars DataFrame."""
        self.ensure_loaded()
        return _instruments_frame(
            (
                inst
                for inst in self._instruments.values()
                if not supported_only or inst.is_supported
            ),
            self._delisted_at,
        )


    def to_provider_dataframe(self, asset_type: str = "stock") -> pl.DataFrame:
        """Export standardized DataFrame adhering to MarketDataProvider contract.

        Columns: ['symbol', 'name', 'exchange', 'asset_type', 'source', 'list_date', 'status']
        """
        self.ensure_loaded()
        target_type = asset_type.lower()
        rows = []
        for inst in self._instruments.values():
            if not inst.is_supported or inst.listing_status != "active":
                continue
            if target_type in ("stock", "etf") and inst.instrument_type != target_type:
                continue
            rows.append({
                "symbol": inst.symbol,
                "name": inst.name,
                "code": inst.code,
                "exchange": inst.exchange,
                "asset_type": inst.instrument_type,
                "source": inst.source,
                "list_date": inst.listing_date,
                "status": inst.listing_status,
                "currency": inst.currency,
                "lot_size": inst.lot_size,
            })
        return pl.DataFrame(rows)
