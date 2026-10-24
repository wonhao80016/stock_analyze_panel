"""Stage 5D: research context windows and evidence accounting.

Covers B055 (5D/20D returns and 20D extremes counted in trading days), B212 (the close
basis of the 20D extremes is labelled), B213 (institutional 5D sums the last five trading
days) and B215 (evidence counts and statuses follow the fields actually present).
"""
from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import polars as pl

from app.taiwan.ai_research import build_evidence_registry
from app.taiwan.research_context import TaiwanStockResearchContextService

SYMBOL = "TEST.TWSE"


def _sessions(n: int, last: date = date(2026, 9, 30)) -> list[date]:
    out: list[date] = []
    d = last
    while len(out) < n:
        if d.weekday() < 5:
            out.append(d)
        d -= timedelta(days=1)
    return sorted(out)


DATES = _sessions(25)
TARGET = DATES[-1]


def _store(frame: pl.DataFrame, dates: list[date]) -> MagicMock:
    store = MagicMock()
    store._data_dir = None  # served through read_range, not partition files
    store.available_dates.return_value = list(dates)

    def read_range(symbols, start, end):
        if frame.is_empty():
            return frame
        in_range = (pl.col("date") >= start) & (pl.col("date") <= end)
        if symbols is None:
            return frame.filter(in_range)
        return frame.filter(pl.col("symbol").is_in(list(symbols)) & in_range)

    store.read_range.side_effect = read_range
    return store


def _daily(closes: dict[date, float | None]) -> pl.DataFrame:
    rows = sorted(closes.items())
    return pl.DataFrame(
        {
            "symbol": [SYMBOL] * len(rows),
            "date": [d for d, _ in rows],
            "open": [c for _, c in rows],
            "high": [c for _, c in rows],
            "low": [c for _, c in rows],
            "close": [c for _, c in rows],
            "volume": [0.0 if c is None else 1000.0 for _, c in rows],
            "amount": [0.0 if c is None else 1000.0 * c for _, c in rows],
        },
        schema={
            "symbol": pl.Utf8, "date": pl.Date, "open": pl.Float64, "high": pl.Float64,
            "low": pl.Float64, "close": pl.Float64, "volume": pl.Float64, "amount": pl.Float64,
        },
    )


def _instrument() -> MagicMock:
    inst = MagicMock()
    inst.symbol = SYMBOL
    inst.code = "TEST"
    inst.name = "測試股"
    inst.exchange = "TWSE"
    inst.instrument_type = "stock"
    inst.industry = "半導體業"
    inst.currency = "TWD"
    inst.listing_status = "active"
    inst.listing_date = "2020-01-01"
    inst.updated_at = "2026-09-30"
    inst.leverage_multiplier = 1.0
    sm = MagicMock()
    sm.get_instrument.return_value = inst
    return sm


def _breadth(adv: int = 1, dec: int = 1, flat: int = 0):
    return SimpleNamespace(
        turnover=1.0, advance_count=adv, decline_count=dec, flat_count=flat,
        upper_limit_count=0, lower_limit_count=0,
    )


def _market_svc() -> MagicMock:
    snap = SimpleNamespace(
        trade_date=str(TARGET),
        market_totals=_breadth(60, 40, 0),
        by_instrument=SimpleNamespace(stock=_breadth(30, 70, 0), etf=_breadth(30, 0, 0)),
        by_exchange=SimpleNamespace(twse=_breadth(), tpex=_breadth()),
        institutional=SimpleNamespace(total_net_amount=0),
        margin=SimpleNamespace(margin_balance_change=0.0),
        data_quality=SimpleNamespace(overall_status="complete"),
    )
    svc = MagicMock()
    svc.get_snapshot.return_value = snap
    return svc


def _industry_svc() -> MagicMock:
    svc = MagicMock()
    svc.get_snapshot.return_value = SimpleNamespace(
        industries=[], trade_date=str(TARGET), data_quality=SimpleNamespace(overall_status="complete"),
    )
    return svc


def _empty_frame_store(dates: list[date]) -> MagicMock:
    return _store(pl.DataFrame(), dates)


def _service(daily: pl.DataFrame, *, inst: pl.DataFrame | None = None, inst_dates=None,
             margin: pl.DataFrame | None = None, margin_dates=None) -> TaiwanStockResearchContextService:
    return TaiwanStockResearchContextService(
        daily_store=_store(daily, DATES),
        inst_store=_store(inst, inst_dates or DATES) if inst is not None else _empty_frame_store([]),
        margin_store=_store(margin, margin_dates or DATES) if margin is not None else _empty_frame_store([]),
        security_master=_instrument(),
        market_intel_svc=_market_svc(),
        industry_intel_svc=_industry_svc(),
    )


# ── B055 ──────────────────────────────────────────────────────


def test_returns_and_extremes_are_counted_in_trading_days_not_priced_rows():
    closes: dict[date, float | None] = dict.fromkeys(DATES, 100.0)
    closes[DATES[3]] = 200.0  # 21 priced rows back once the halt below is skipped
    closes[DATES[4]] = 50.0  # exactly 20 trading days before the target
    closes[DATES[10]] = 120.0
    closes[DATES[19]] = None  # 5 trading days before the target: halted
    ctx = _service(_daily(closes)).get_research_context(SYMBOL, TARGET)
    pc = ctx.price_context

    assert pc.close == 100.0
    assert pc.return_5d is None
    assert pc.return_20d == 1.0
    assert pc.high_20d == 120.0
    assert pc.low_20d == 100.0


# ── B212 ──────────────────────────────────────────────────────


def test_twenty_day_extremes_declare_their_close_basis():
    ctx = _service(_daily(dict.fromkeys(DATES, 100.0))).get_research_context(SYMBOL, TARGET)

    assert "close" in (ctx.price_context.meta.formula or "")
    payload, keys, _ = build_evidence_registry(ctx, None)
    assert payload["price_context"]["high_low_20d_basis"] == "close"
    assert "price_context.high_low_20d_basis" in keys


# ── B213 ──────────────────────────────────────────────────────


def _inst(rows: list[tuple[str, date, float]]) -> pl.DataFrame:
    return pl.DataFrame({
        "symbol": [r[0] for r in rows],
        "date": [r[1] for r in rows],
        "foreign_net": [r[2] for r in rows],
        "investment_trust_net": [r[2] for r in rows],
        "dealer_net": [r[2] for r in rows],
    })


def test_institutional_five_day_sum_uses_the_last_five_trading_days():
    last5 = DATES[-5:]
    rows = [(SYMBOL, DATES[15], 1000.0)] + [(SYMBOL, d, 10.0) for d in last5 if d != last5[2]]
    # Another TWSE row on every session shows the exchange was published each day.
    rows += [("OTHER.TWSE", d, 1.0) for d in DATES]
    ctx = _service(_daily(dict.fromkeys(DATES, 100.0)), inst=_inst(rows)).get_research_context(SYMBOL, TARGET)
    ic = ctx.institutional_context

    assert ic.foreign_net_5d == 40.0
    assert ic.investment_trust_net_5d == 40.0
    assert ic.coverage_days_5d == 4


def test_institutional_twenty_day_sum_uses_the_last_twenty_trading_days():
    rows = [(SYMBOL, DATES[2], 1000.0)] + [(SYMBOL, d, 10.0) for d in DATES[-20:] if d != DATES[-7]]
    rows += [("OTHER.TWSE", d, 1.0) for d in DATES]
    ctx = _service(_daily(dict.fromkeys(DATES, 100.0)), inst=_inst(rows)).get_research_context(SYMBOL, TARGET)
    ic = ctx.institutional_context

    assert ic.foreign_net_20d == 190.0
    assert ic.coverage_days_20d == 19


def test_a_session_missing_the_whole_exchange_leaves_the_sums_unknown():
    missing_day = DATES[-3]
    rows = [(SYMBOL, d, 10.0) for d in DATES if d != missing_day]
    rows += [("OTHER.TWSE", d, 1.0) for d in DATES if d != missing_day]
    # The TPEx table of that day was stored; the TWSE table was not.
    rows += [("OTC.TPEX", d, 1.0) for d in DATES]
    ctx = _service(_daily(dict.fromkeys(DATES, 100.0)), inst=_inst(rows)).get_research_context(SYMBOL, TARGET)
    ic = ctx.institutional_context

    assert ic.foreign_net_5d is None
    assert ic.investment_trust_net_5d is None
    assert ic.foreign_net_20d is None


# ── B176 (AI payload) / F7 ───────────────────────────────────


def test_market_advance_ratio_sent_to_ai_counts_stocks_only():
    ctx = _service(_daily(dict.fromkeys(DATES, 100.0))).get_research_context(SYMBOL, TARGET)
    payload, _, _ = build_evidence_registry(ctx, None)

    assert payload["market_context"]["advance_ratio"] == 0.3


def test_market_and_industry_snapshots_use_the_priced_session():
    svc = _service(_daily(dict.fromkeys(DATES[:-1], 100.0)))
    svc.daily_store.available_dates.return_value = DATES[:-1]

    ctx = svc.get_research_context(SYMBOL, TARGET)

    assert ctx.price_context.trade_date == str(DATES[-2])
    assert svc.market_intel_svc.get_snapshot.call_args.args[0] == DATES[-2]
    assert svc.industry_intel_svc.get_snapshot.call_args.args[0] == DATES[-2]


# ── B215 ──────────────────────────────────────────────────────


def _full_inputs():
    inst = pl.DataFrame({
        "symbol": [SYMBOL] * len(DATES),
        "date": DATES,
        "foreign_net": [1.0] * len(DATES),
        "investment_trust_net": [1.0] * len(DATES),
        "dealer_net": [1.0] * len(DATES),
    })
    margin = pl.DataFrame({
        "symbol": [SYMBOL] * len(DATES),
        "date": DATES,
        "margin_balance": [1000.0] * len(DATES),
        "margin_change": [1.0] * len(DATES),
        "short_balance": [100.0] * len(DATES),
        "short_change": [1.0] * len(DATES),
        "short_margin_ratio": [10.0] * len(DATES),
    })
    return inst, margin


def _published(partition_date, columns, symbols):
    values = {"ma5": 100.0, "ma20": 100.0, "ma60": 100.0, "vol_ratio_5d": 1.0, "rsi_14": 50.0,
              "close": 100.0, "raw_close": 100.0}
    return pl.DataFrame({c: [values[c]] for c in columns})


def test_evidence_counts_and_status_follow_a_missing_target_price():
    inst, margin = _full_inputs()
    full_closes = dict.fromkeys(DATES, 100.0)
    no_target = dict.fromkeys(DATES[:-1], 100.0)
    with patch("app.taiwan.research_context.read_enriched_partition", side_effect=_published):
        full = _service(_daily(full_closes), inst=inst, margin=margin).get_research_context(SYMBOL, TARGET)
        # Other symbols filled the target partition; this one has no row on it.
        missing = _service(_daily(no_target), inst=inst, margin=margin).get_research_context(SYMBOL, TARGET)

    assert full.data_quality.overall_status == "complete"
    assert missing.price_context.close is None
    assert missing.data_quality.overall_status == "partial"
    assert "price" in missing.evidence_summary.missing_sections
    assert missing.evidence_summary.known_fields_count < full.evidence_summary.known_fields_count
    assert missing.evidence_summary.derived_fields_count < full.evidence_summary.derived_fields_count
    assert missing.evidence_summary.missing_fields_count > full.evidence_summary.missing_fields_count


def test_unread_realtime_and_monitor_sections_are_not_reported_as_known():
    ctx = _service(_daily(dict.fromkeys(DATES, 100.0))).get_research_context(SYMBOL, TARGET)

    assert ctx.realtime_context.status == "unavailable"
    assert ctx.realtime_context.meta.classification == "MISSING"
    assert ctx.monitor_context.meta.classification == "MISSING"
