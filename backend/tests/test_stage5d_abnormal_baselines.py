"""Stage 5D: abnormal diagnostics baselines, zero medians, populations and threshold rounding.

Covers B040 (halted rows in the 5-session baseline), B041/B042 (zero 20-session margin
medians), B176 (market advance_ratio population), B177 (T86 missing rows count as zero)
and B178/B179 (thresholds compared before rounding).
"""
from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import MagicMock

import polars as pl

from app.taiwan.abnormal_diagnostics import TaiwanAbnormalDiagnosticsService

TARGET = date(2026, 9, 30)


def _sessions(n: int) -> list[date]:
    """n weekday sessions strictly before TARGET, oldest first."""
    out: list[date] = []
    d = TARGET
    while len(out) < n:
        d -= timedelta(days=1)
        if d.weekday() < 5:
            out.append(d)
    return sorted(out)


def _security_master(symbols: list[str], industry: str | None = "半導體業") -> MagicMock:
    sm = MagicMock()
    sm.get_universe.side_effect = lambda universe_type: list(symbols) if universe_type.name == "TAIWAN_STOCKS" else []
    inst_map = {}
    for sym in symbols:
        inst = MagicMock()
        inst.symbol = sym
        inst.code = sym.split(".")[0]
        inst.name = sym
        inst.exchange = sym.split(".")[1]
        inst.instrument_type = "stock"
        inst.industry = industry
        inst.leverage_multiplier = 1.0
        inst.listing_date = None
        inst_map[sym] = inst
    sm.get_instrument.side_effect = inst_map.get
    sm.to_dataframe.return_value = pl.DataFrame({
        "symbol": symbols,
        "name": symbols,
        "exchange": [s.split(".")[1] for s in symbols],
        "instrument_type": ["stock"] * len(symbols),
        "industry": [industry] * len(symbols),
        "listing_status": ["active"] * len(symbols),
    })
    return sm


def _store(frame: pl.DataFrame) -> MagicMock:
    store = MagicMock()
    dates = sorted(set(frame["date"].to_list())) if not frame.is_empty() else []
    store.available_dates.return_value = dates
    store.incomplete_dates.return_value = []
    store.provisional_dates.return_value = []

    def read_range(symbols, start, end):
        if frame.is_empty():
            return frame
        return frame.filter(
            pl.col("symbol").is_in(list(symbols)) & (pl.col("date") >= start) & (pl.col("date") <= end)
        )

    store.read_range.side_effect = read_range
    return store


def _breadth(adv: int, dec: int, flat: int) -> SimpleNamespace:
    return SimpleNamespace(advance_count=adv, decline_count=dec, flat_count=flat, turnover=1.0)


def _market_svc(totals=(60, 40, 0), stock=(30, 70, 0)) -> MagicMock:
    snap = SimpleNamespace(
        trade_date=str(TARGET),
        market_totals=_breadth(*totals),
        by_instrument=SimpleNamespace(stock=_breadth(*stock), etf=_breadth(30, 0, 0)),
        data_quality=SimpleNamespace(overall_status="complete"),
    )
    svc = MagicMock()
    svc.get_snapshot.return_value = snap
    return svc


def _industry_svc(industries=()) -> MagicMock:
    svc = MagicMock()
    svc.get_snapshot.return_value = SimpleNamespace(industries=list(industries))
    return svc


def _daily_rows(symbol: str, rows: list[tuple[date, float | None, float]]) -> pl.DataFrame:
    """rows: (date, close or None for a halted session, volume)."""
    return pl.DataFrame(
        {
            "symbol": [symbol] * len(rows),
            "date": [r[0] for r in rows],
            "open": [r[1] for r in rows],
            "high": [r[1] for r in rows],
            "low": [r[1] for r in rows],
            "close": [r[1] for r in rows],
            "volume": [r[2] for r in rows],
            "amount": [r[2] * (r[1] or 0.0) for r in rows],
        },
        schema={
            "symbol": pl.Utf8, "date": pl.Date, "open": pl.Float64, "high": pl.Float64,
            "low": pl.Float64, "close": pl.Float64, "volume": pl.Float64, "amount": pl.Float64,
        },
    )


def _service(daily: pl.DataFrame, symbols: list[str], *, inst=None, margin=None,
             market=None, industry=None, industry_name: str | None = "半導體業"):
    empty = pl.DataFrame()
    return TaiwanAbnormalDiagnosticsService(
        daily_store=_store(daily),
        inst_store=_store(inst if inst is not None else empty),
        margin_store=_store(margin if margin is not None else empty),
        security_master=_security_master(symbols, industry_name),
        market_intel_svc=market or _market_svc(),
        industry_intel_svc=industry or _industry_svc(),
    )


def _item(snap, symbol: str):
    return next(i for i in snap.items if i.symbol == symbol)


def _signal(item, sig_type: str):
    return next((s for s in item.signals if s.type == sig_type), None)


# ── B040 ──────────────────────────────────────────────────────


def test_halted_sessions_are_excluded_from_the_five_session_volume_baseline():
    prior = _sessions(6)
    # The second-latest prior session is a halt: OHLC null and zero volume.
    rows = [(d, 10.0, 100.0) for d in prior]
    rows[4] = (prior[4], None, 0.0)
    rows.append((TARGET, 10.0, 190.0))
    svc = _service(_daily_rows("A.TWSE", rows), ["A.TWSE"])

    item = _item(svc.get_diagnostics(target_date=TARGET, include_all=True), "A.TWSE")

    assert item.volume_ratio_5d == 1.9
    assert _signal(item, "VOLUME_SPIKE") is None
    assert _signal(item, "TURNOVER_SPIKE") is None


def test_previous_close_falls_back_to_the_last_traded_session_after_a_halt():
    prior = _sessions(6)
    rows = [(d, 10.0, 100.0) for d in prior]
    rows[-1] = (prior[-1], None, 0.0)
    rows.append((TARGET, 11.0, 100.0))
    svc = _service(_daily_rows("A.TWSE", rows), ["A.TWSE"])

    item = _item(svc.get_diagnostics(target_date=TARGET, include_all=True), "A.TWSE")

    assert item.previous_close == 10.0
    assert item.change_pct == 0.1
    move = _signal(item, "PRICE_MOVE")
    assert move is not None and move.subtype == "UP"


# ── B041 / B042 ───────────────────────────────────────────────


def _margin_frame(symbol: str, prior: list[date], margin_changes: list[float], short_changes: list[float],
                  target_margin: float, target_short: float) -> pl.DataFrame:
    dates = [*prior, TARGET]
    return pl.DataFrame({
        "symbol": [symbol] * len(dates),
        "date": dates,
        "margin_change": [*margin_changes, target_margin],
        "short_change": [*short_changes, target_short],
        "short_margin_ratio": [1.0] * len(dates),
        "margin_balance": [1_000_000.0] * len(dates),
    })


def test_zero_twenty_session_median_still_reports_margin_and_short_surges():
    prior = _sessions(20)
    daily = _daily_rows("A.TWSE", [(d, 10.0, 100.0) for d in prior] + [(TARGET, 10.0, 100.0)])
    margin = _margin_frame("A.TWSE", prior, [0.0] * 20, [0.0] * 20, 600_000.0, -150_000.0)
    svc = _service(daily, ["A.TWSE"], margin=margin)

    item = _item(svc.get_diagnostics(target_date=TARGET, include_all=True), "A.TWSE")

    margin_sig = _signal(item, "MARGIN_SURGE")
    assert margin_sig is not None
    assert margin_sig.status == "zero_baseline"
    assert margin_sig.baseline == 0.0
    assert margin_sig.ratio is None
    assert margin_sig.subtype == "INCREASE"
    assert margin_sig.threshold == 500_000.0
    assert "3.0" not in margin_sig.formula and "500k" in margin_sig.formula
    short_sig = _signal(item, "SHORT_SURGE")
    assert short_sig is not None
    assert short_sig.status == "zero_baseline"
    assert short_sig.subtype == "DECREASE"
    assert short_sig.threshold == 100_000.0
    assert "3.0" not in short_sig.formula and "100k" in short_sig.formula


def test_zero_median_below_the_absolute_floor_is_not_a_surge():
    prior = _sessions(20)
    daily = _daily_rows("A.TWSE", [(d, 10.0, 100.0) for d in prior] + [(TARGET, 10.0, 100.0)])
    margin = _margin_frame("A.TWSE", prior, [0.0] * 20, [0.0] * 20, 400_000.0, 90_000.0)
    svc = _service(daily, ["A.TWSE"], margin=margin)

    item = _item(svc.get_diagnostics(target_date=TARGET, include_all=True), "A.TWSE")

    assert _signal(item, "MARGIN_SURGE") is None
    assert _signal(item, "SHORT_SURGE") is None


# ── B176 ──────────────────────────────────────────────────────


def test_market_advance_ratio_uses_the_stock_population_like_the_industry_ratio():
    prior = _sessions(5)
    daily = _daily_rows("A.TWSE", [(d, 10.0, 100.0) for d in prior] + [(TARGET, 10.0, 100.0)])
    svc = _service(daily, ["A.TWSE"], market=_market_svc(totals=(60, 40, 0), stock=(30, 70, 0)))

    item = _item(svc.get_diagnostics(target_date=TARGET, include_all=True), "A.TWSE")

    assert item.market_context.advance_ratio == 0.3


# ── B177 ──────────────────────────────────────────────────────


def _inst_frame(rows: list[tuple[str, date, float]]) -> pl.DataFrame:
    return pl.DataFrame({
        "symbol": [r[0] for r in rows],
        "date": [r[1] for r in rows],
        "foreign_net": [r[2] for r in rows],
        "investment_trust_net": [0.0] * len(rows),
        "dealer_net": [0.0] * len(rows),
    })


def test_missing_t86_rows_on_published_sessions_count_as_zero_in_the_flow_baseline():
    prior = _sessions(20)
    daily = pl.concat([
        _daily_rows(sym, [(d, 10.0, 100.0) for d in prior] + [(TARGET, 10.0, 100.0)])
        for sym in ("A.TWSE", "B.TWSE")
    ])
    # A has institutional rows on only 14 of 20 published sessions; B keeps every session published.
    inst_rows = [("A.TWSE", d, 20_000.0) for d in prior[:14]]
    inst_rows += [("B.TWSE", d, 1_000.0) for d in prior]
    inst_rows += [("A.TWSE", TARGET, 300_000.0), ("B.TWSE", TARGET, 1_000.0)]
    svc = _service(daily, ["A.TWSE", "B.TWSE"], inst=_inst_frame(inst_rows))

    item = _item(svc.get_diagnostics(target_date=TARGET, include_all=True), "A.TWSE")

    flow = _signal(item, "FOREIGN_FLOW_SPIKE")
    assert flow is not None
    assert flow.baseline == 14_000.0
    assert flow.valid_sessions == 20
    assert flow.ratio == round(300_000.0 / 14_000.0, 2)


# ── B178 / B179 ───────────────────────────────────────────────


def test_volume_ratio_just_below_the_threshold_does_not_trigger_after_rounding():
    prior = _sessions(5)
    daily = _daily_rows("A.TWSE", [(d, 10.0, 1_000.0) for d in prior] + [(TARGET, 10.0, 1_996.0)])
    svc = _service(daily, ["A.TWSE"])

    item = _item(svc.get_diagnostics(target_date=TARGET, include_all=True), "A.TWSE")

    assert item.volume_ratio_5d == 2.0
    assert _signal(item, "VOLUME_SPIKE") is None


def test_volume_ratio_exactly_at_the_threshold_still_triggers():
    prior = _sessions(5)
    daily = _daily_rows("A.TWSE", [(d, 10.0, 1_000.0) for d in prior] + [(TARGET, 10.0, 2_000.0)])
    svc = _service(daily, ["A.TWSE"])

    item = _item(svc.get_diagnostics(target_date=TARGET, include_all=True), "A.TWSE")

    assert _signal(item, "VOLUME_SPIKE") is not None


def test_foreign_multiple_just_below_three_does_not_trigger_after_rounding():
    prior = _sessions(20)
    daily = _daily_rows("A.TWSE", [(d, 10.0, 100.0) for d in prior] + [(TARGET, 10.0, 100.0)])
    inst = _inst_frame([("A.TWSE", d, 100_000.0) for d in prior] + [("A.TWSE", TARGET, 299_600.0)])
    svc = _service(daily, ["A.TWSE"], inst=inst)

    item = _item(svc.get_diagnostics(target_date=TARGET, include_all=True), "A.TWSE")

    assert _signal(item, "FOREIGN_FLOW_SPIKE") is None


def test_short_margin_ratio_delta_just_below_five_points_does_not_trigger():
    prior = _sessions(20)
    daily = _daily_rows("A.TWSE", [(d, 10.0, 100.0) for d in prior] + [(TARGET, 10.0, 100.0)])
    margin = pl.DataFrame({
        "symbol": ["A.TWSE"] * 21,
        "date": [*prior, TARGET],
        "margin_change": [0.0] * 21,
        "short_change": [0.0] * 21,
        "short_margin_ratio": [10.0] * 20 + [14.996],
        "margin_balance": [1_000_000.0] * 21,
    })
    svc = _service(daily, ["A.TWSE"], margin=margin)

    item = _item(svc.get_diagnostics(target_date=TARGET, include_all=True), "A.TWSE")

    assert _signal(item, "SHORT_MARGIN_RATIO_SPIKE") is None


def test_relative_strength_gap_just_below_ten_points_does_not_trigger():
    prior = _sessions(5)
    closes = [100.0, 100.0, 100.0, 100.0, 100.0]
    rows = [(d, c, 100.0) for d, c in zip(prior, closes, strict=True)] + [(TARGET, 109.996, 100.0)]
    industry = SimpleNamespace(
        industry="半導體業", average_return_5d=0.0, turnover_share=0.1, advance_ratio=0.5,
        relative_strength_5d=0.0, relative_strength_20d=0.0,
        average_return_5d_excluding=lambda symbol: 0.0,
    )
    svc = _service(_daily_rows("A.TWSE", rows), ["A.TWSE"], industry=_industry_svc([industry]))

    item = _item(svc.get_diagnostics(target_date=TARGET, include_all=True), "A.TWSE")

    assert _signal(item, "RELATIVE_STRENGTH_OUTLIER") is None


# ── F5: halted sessions in the institutional baseline ────────


def test_halted_sessions_are_excluded_from_the_institutional_baseline():
    prior = _sessions(20)
    halted = set(prior[:4])
    daily = pl.concat([
        _daily_rows("A.TWSE", [(d, None if d in halted else 10.0, 0.0 if d in halted else 100.0) for d in prior]
                    + [(TARGET, 10.0, 100.0)]),
        _daily_rows("B.TWSE", [(d, 10.0, 100.0) for d in [*prior, TARGET]]),
    ])
    traded = [d for d in prior if d not in halted]
    inst_rows = [("A.TWSE", d, 20_000.0) for d in traded[:14]]
    inst_rows += [("B.TWSE", d, 1_000.0) for d in prior]
    inst_rows += [("A.TWSE", TARGET, 300_000.0), ("B.TWSE", TARGET, 1_000.0)]
    svc = _service(daily, ["A.TWSE", "B.TWSE"], inst=_inst_frame(inst_rows))

    item = _item(svc.get_diagnostics(target_date=TARGET, include_all=True), "A.TWSE")

    flow = _signal(item, "FOREIGN_FLOW_SPIKE")
    assert flow is not None
    assert flow.valid_sessions == 16
    assert flow.baseline == 17_500.0


# ── B202: the industry baseline leaves the stock itself out ──


def test_relative_strength_compares_against_the_other_members_only():
    from app.taiwan.industry_intelligence import IndustryMetrics

    prior = _sessions(5)
    rows = [(d, 100.0, 100.0) for d in prior] + [(TARGET, 112.0, 100.0)]
    # Including the stock, the industry averages +4% and the gap is only 8 points.
    industry = IndustryMetrics(industry="半導體業", average_return_5d=0.04)
    industry._return_5d_by_symbol = {"A.TWSE": 0.12, "B.TWSE": 0.0, "C.TWSE": 0.0}
    svc = _service(_daily_rows("A.TWSE", rows), ["A.TWSE"], industry=_industry_svc([industry]))

    item = _item(svc.get_diagnostics(target_date=TARGET, include_all=True), "A.TWSE")

    rso = _signal(item, "RELATIVE_STRENGTH_OUTLIER")
    assert rso is not None
    assert rso.subtype == "LEADER"
    assert rso.baseline == 0.0
