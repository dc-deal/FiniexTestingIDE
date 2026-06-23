"""
Report aggregators (#391) — the measures over the per-unit report rows.

One home for every section's aggregation: a pure `aggregate_*(rows) → aggregate(s)` over the
already-mapped model rows (the unified per-unit projection). Keeping the aggregators together
(facts → measures) makes the pattern consistent and is the seam a future run-wide `RunSummary`
composes from (#390 prework). Counts aggregate currency-agnostically; P&L-denominated figures
group per account currency; ratios (win rate / profit factor) are recomputed from the summed
components, never summed.
"""

from typing import Dict, List

from python.framework.types.api.report_types import (
    AggregatedPortfolioRow, AggregatedPortfolioSpotScenarioRow,
    ExecutionStatsRow, ExecutionStatsTotals, PendingOrdersUnitRow, PortfolioAggregateRow,
    PortfolioUnitRow, ProfilingAggregate, ProfilingBottleneckRow, ProfilingOperationRow,
    ProfilingUnitRow, TradeAnalytics, TradeHistoryRow, TradeScenarioTotals, WorkerDecisionUnitRow,
    WorkerStatRow)
from python.framework.types.scenario_types.scenario_set_performance_types import EXPECTED_OPERATIONS


# --- Trade analytics (per account currency) -------------------------------------------

def aggregate_trade_analytics(rows: List[TradeHistoryRow]) -> List[TradeAnalytics]:
    """
    Per-currency trade analytics (#389/#393): group the rows by account currency and compute
    one TradeAnalytics each, so the P&L-denominated fields (MAE/MFE) never mix currencies.

    Args:
        rows: The report's trade rows (already filtered)

    Returns:
        One TradeAnalytics per currency present (sorted), [] for no rows
    """
    groups: Dict[str, List[TradeHistoryRow]] = {}
    for row in rows:
        groups.setdefault(row.currency, []).append(row)
    return [_trade_analytics(groups[c]) for c in sorted(groups)]


def _trade_analytics(rows: List[TradeHistoryRow]) -> TradeAnalytics:
    """Aggregate the per-row analytics (#389) for ONE currency group."""
    r_rows = [r for r in rows if r.r_multiple is not None]
    winners = [r for r in rows if r.net_pnl > 0]
    losers = [r for r in rows if r.net_pnl < 0]
    return TradeAnalytics(
        currency=rows[0].currency if rows else '',
        trade_count=len(rows),
        expectancy=_mean([r.r_multiple for r in r_rows]),
        avg_win_r=_mean([r.r_multiple for r in r_rows if r.net_pnl > 0]),
        avg_loss_r=_mean([r.r_multiple for r in r_rows if r.net_pnl < 0]),
        r_trade_count=len(r_rows),
        avg_mae_winners=_mean([r.mae_pnl for r in winners]),
        avg_mae_losers=_mean([r.mae_pnl for r in losers]),
        avg_mfe_losers=_mean([r.mfe_pnl for r in losers]),
        gross_pnl=sum(r.gross_pnl for r in rows),
        net_pnl=sum(r.net_pnl for r in rows),
        total_fees=sum(r.total_fees for r in rows),
    )


# --- Trade per-scenario totals (the per-scenario table footer) ------------------------

def aggregate_trade_scenario_totals(rows: List[TradeHistoryRow]) -> List[TradeScenarioTotals]:
    """
    Per-scenario trade-table totals (the footer line): group the rows by `scenario_name`
    and sum gross / net / fees, so the console (and the API) read the footer off the model.

    Args:
        rows: The report's trade rows (already filtered)

    Returns:
        One TradeScenarioTotals per scenario present (first-appearance order)
    """
    groups: Dict[str, List[TradeHistoryRow]] = {}
    for row in rows:
        groups.setdefault(row.scenario_name, []).append(row)
    return [
        TradeScenarioTotals(
            scenario_name=name,
            currency=group[0].currency,
            trade_count=len(group),
            gross_pnl=sum(r.gross_pnl for r in group),
            net_pnl=sum(r.net_pnl for r in group),
            total_fees=sum(r.total_fees for r in group),
            total_swap=sum(r.swap_cost for r in group),
        )
        for name, group in groups.items()
    ]


# --- Execution totals (currency-agnostic counts) --------------------------------------

def aggregate_execution_totals(rows: List[ExecutionStatsRow]) -> ExecutionStatsTotals:
    """Sum the per-unit order counts (currency-agnostic) into one totals object."""
    return ExecutionStatsTotals(
        orders_sent=sum(r.orders_sent for r in rows),
        orders_executed=sum(r.orders_executed for r in rows),
        orders_rejected=sum(r.orders_rejected for r in rows),
        sl_tp_triggered=sum(r.sl_tp_triggered for r in rows),
    )


# --- Portfolio roll-up (per account currency) -----------------------------------------

def aggregate_portfolio_by_currency(
    rows: List[PortfolioUnitRow]) -> List[PortfolioAggregateRow]:
    """
    Per-currency portfolio roll-up: group the unit rows by account currency, sum the
    additive headline figures, and recompute the ratios (win rate / profit factor) from the
    sums — never sum ratios. Drawdown is the worst (largest magnitude) across the group.
    Mirrors the console `PortfolioAggregator` formulas so report and console stay identical.

    Args:
        rows: The portfolio per-unit rows

    Returns:
        One PortfolioAggregateRow per currency present (sorted)
    """
    groups: Dict[str, List[PortfolioUnitRow]] = {}
    for row in rows:
        groups.setdefault(row.currency, []).append(row)
    return [_portfolio_aggregate(c, groups[c]) for c in sorted(groups)]


def _portfolio_aggregate(currency: str, rows: List[PortfolioUnitRow]) -> PortfolioAggregateRow:
    """Roll up one currency group into a headline aggregate row."""
    total_trades = sum(r.total_trades for r in rows)
    winning_trades = sum(r.winning_trades for r in rows)
    losing_trades = sum(r.losing_trades for r in rows)
    total_profit = sum(r.total_profit for r in rows)
    total_loss = sum(r.total_loss for r in rows)
    win_rate = winning_trades / total_trades if total_trades > 0 else 0.0
    profit_factor = total_profit / total_loss if total_loss > 0 else (
        0.0 if total_profit == 0 else float('inf'))
    max_drawdown = 0.0
    for r in rows:
        if abs(r.max_drawdown) > abs(max_drawdown):
            max_drawdown = r.max_drawdown
    return PortfolioAggregateRow(
        currency=currency,
        unit_count=len(rows),
        total_trades=total_trades,
        winning_trades=winning_trades,
        losing_trades=losing_trades,
        win_rate=win_rate,
        profit_factor=profit_factor,
        total_profit=total_profit,
        total_loss=total_loss,
        net_profit=total_profit - total_loss,
        max_drawdown=max_drawdown,
        total_fees=sum(r.total_fees for r in rows),
    )


def aggregate_full_portfolio(
    rows: List[PortfolioUnitRow],
    exec_by_name: Dict[str, ExecutionStatsRow],
    pending_by_name: Dict[str, PendingOrdersUnitRow],
    currency: str,
    is_spot: bool,
    label: str,
) -> AggregatedPortfolioRow:
    """
    Roll up one currency (or margin/spot sub-) group into the rich aggregate (#397). Composes the
    lean headline (`_portfolio_aggregate`, byte-identical) and adds the cross-domain measures —
    balances, cost split, per-currency execution + pending (weighted-avg latency), and the spot
    dual-balance. Mirrors `PortfolioAggregator` + the executive's inline derivations exactly.

    Args:
        rows: The portfolio per-unit rows of this group
        exec_by_name / pending_by_name: execution / pending rows keyed by unit name
        currency / is_spot / label: group identity (label = '' | 'Margin' | 'Spot')

    Returns:
        AggregatedPortfolioRow
    """
    headline = _portfolio_aggregate(currency, rows)
    total_trades = headline.total_trades
    winning, losing = headline.winning_trades, headline.losing_trades
    total_profit, total_loss = headline.total_profit, headline.total_loss

    # Worst-magnitude drawdown + peak equity, with scenario attribution (order-preserving)
    max_dd = 0.0
    max_dd_scn = ''
    max_eq = 0.0
    max_eq_scn = ''
    for r in rows:
        if abs(r.max_drawdown) > abs(max_dd):
            max_dd, max_dd_scn = r.max_drawdown, r.name
        if r.max_equity > max_eq:
            max_eq, max_eq_scn = r.max_equity, r.name

    initial = sum(r.initial_balance for r in rows)
    final = sum(r.current_balance for r in rows)
    count = len(rows)
    balance_pnl = final - initial

    total_spread = sum(r.total_spread_cost for r in rows)

    ex = [exec_by_name[r.name] for r in rows if r.name in exec_by_name]
    pend = [pending_by_name[r.name] for r in rows if r.name in pending_by_name]
    lat = [p for p in pend if p.avg_latency_ms is not None]
    lat_count = sum(p.latency_count for p in lat)

    return AggregatedPortfolioRow(
        headline=headline,
        is_spot=is_spot,
        label=label,
        total_long_trades=sum(r.total_long_trades for r in rows),
        total_short_trades=sum(r.total_short_trades for r in rows),
        avg_win=total_profit / winning if winning > 0 else 0.0,
        avg_loss=abs(total_loss) / losing if losing > 0 else 0.0,
        initial_balance=initial,
        final_balance=final,
        avg_initial=initial / count if count > 0 else 0.0,
        balance_pnl=balance_pnl,
        balance_pnl_pct=(balance_pnl / initial * 100) if initial > 0 else 0.0,
        recovery_factor=balance_pnl / abs(max_dd) if max_dd != 0 else 0.0,
        max_dd_pct=(max_dd / max_eq * 100) if max_eq > 0 else 0.0,
        max_drawdown_scenario=max_dd_scn,
        max_equity=max_eq,
        max_equity_scenario=max_eq_scn,
        total_spread_cost=total_spread,
        total_commission=sum(r.total_commission for r in rows),
        total_swap=sum(r.total_swap for r in rows),
        maker_fee=sum(r.maker_fee for r in rows),
        taker_fee=sum(r.taker_fee for r in rows),
        avg_spread=total_spread / total_trades if total_trades > 0 else 0.0,
        orders_sent=sum(e.orders_sent for e in ex),
        orders_executed=sum(e.orders_executed for e in ex),
        orders_rejected=sum(e.orders_rejected for e in ex),
        sl_tp_triggered=sum(e.sl_tp_triggered for e in ex),
        pending_total_resolved=sum(p.total_resolved for p in pend),
        pending_total_filled=sum(p.total_filled for p in pend),
        pending_total_rejected=sum(p.total_rejected for p in pend),
        pending_total_timed_out=sum(p.total_timed_out for p in pend),
        pending_total_force_closed=sum(p.total_force_closed for p in pend),
        pending_avg_latency_ms=(
            sum(p.avg_latency_ms * p.latency_count for p in lat) / lat_count) if lat_count > 0 else None,
        pending_min_latency_ms=min((p.min_latency_ms for p in lat), default=None),
        pending_max_latency_ms=max((p.max_latency_ms for p in lat), default=None),
        pending_active_limit_count=sum(len(p.active_limit_orders) for p in pend),
        pending_active_stop_count=sum(len(p.active_stop_orders) for p in pend),
        **_spot_balances(rows, currency) if is_spot else {},
    )


def _spot_balances(rows: List[PortfolioUnitRow], currency: str) -> Dict:
    """Per-scenario spot dual-balance + estimated-value roll-up (mirrors the executive spot block)."""
    spot_rows = []
    total_cur = 0.0
    total_init = 0.0
    has_any_base = False
    for r in rows:
        sym = r.symbol
        base = sym[:-3] if len(sym) >= 6 else ''
        quote = sym[-3:] if len(sym) >= 6 else currency
        quote_bal = r.balances.get(quote, 0.0)
        base_bal = r.balances.get(base, 0.0)
        quote_init = r.initial_balances.get(quote, 0.0)
        base_init = r.initial_balances.get(base, 0.0)
        price = r.last_price
        has_base = price > 0 and base_bal != 0.0
        if has_base:
            est_cur = quote_bal + base_bal * price
            est_init = quote_init + base_init * price
            has_any_base = True
        elif price > 0:
            est_cur, est_init = quote_bal, quote_init
        else:
            est_cur, est_init = 0.0, 0.0
        total_cur += est_cur
        total_init += est_init
        spot_rows.append(AggregatedPortfolioSpotScenarioRow(
            scenario_name=r.name, quote_currency=quote, base_currency=base,
            quote_balance=quote_bal, base_balance=base_bal,
            quote_initial=quote_init, base_initial=base_init, last_price=price,
            est_current=est_cur, est_initial=est_init, has_base_holdings=has_base))
    return dict(
        spot_scenarios=spot_rows, spot_total_est_current=total_cur,
        spot_total_est_initial=total_init, spot_has_base_holdings=has_any_base)


def _mean(values: List[float]) -> float:
    """Mean, or 0.0 for an empty list."""
    return sum(values) / len(values) if values else 0.0


# --- Worker timing (summed across units, #398) ----------------------------------------

def aggregate_worker_totals(rows: List[WorkerDecisionUnitRow]) -> List[WorkerStatRow]:
    """
    Sum per-worker timing across all unit rows (keyed by worker name).

    avg is recomputed from summed total / summed call_count (never averaged); min/max
    are the extremes across units. Returned in descending total-time order.

    Args:
        rows: The per-unit worker/decision rows

    Returns:
        One WorkerStatRow per distinct worker, summed across the units
    """
    acc: Dict[str, Dict] = {}
    for row in rows:
        for w in row.workers:
            a = acc.setdefault(w.worker_name, {
                'worker_type': w.worker_type, 'call_count': 0, 'total_time_ms': 0.0,
                'min_time_ms': None, 'max_time_ms': None})
            a['call_count'] += w.call_count
            a['total_time_ms'] += w.total_time_ms
            a['min_time_ms'] = w.min_time_ms if a['min_time_ms'] is None else min(a['min_time_ms'], w.min_time_ms)
            a['max_time_ms'] = w.max_time_ms if a['max_time_ms'] is None else max(a['max_time_ms'], w.max_time_ms)

    totals = [
        WorkerStatRow(
            worker_type=a['worker_type'], worker_name=name, call_count=a['call_count'],
            total_time_ms=a['total_time_ms'],
            avg_time_ms=(a['total_time_ms'] / a['call_count']) if a['call_count'] else 0.0,
            min_time_ms=a['min_time_ms'] or 0.0, max_time_ms=a['max_time_ms'] or 0.0)
        for name, a in acc.items()
    ]
    totals.sort(key=lambda w: w.total_time_ms, reverse=True)
    return totals


# --- Profiling roll-up (cross-scenario, #399) -----------------------------------------

def aggregate_profiling(rows: List[ProfilingUnitRow], budget_active: bool) -> ProfilingAggregate:
    """
    Roll up the per-unit profiling rows into the run-level aggregate.

    Reproduces the console profiling summary's measures: cross-scenario avg/tick, the most
    common bottleneck, the P5 range, the P95-processing budget recommendation (P95 + 10%),
    the per-operation cross-scenario average call time, and the per-operation bottleneck
    frequency + status. Ratios are recomputed from summed components, never averaged-of-ratios.

    Args:
        rows: The per-unit profiling rows
        budget_active: Whether a tick-processing budget was configured (clipping simulated)

    Returns:
        The run-level ProfilingAggregate
    """
    if not rows:
        return ProfilingAggregate(budget_active=budget_active)

    scenarios = len(rows)
    total_ticks = sum(r.total_ticks for r in rows)
    total_time_ms = sum(r.total_ms for r in rows)
    avg_per_tick_ms = total_time_ms / total_ticks if total_ticks > 0 else 0.0

    # Bottleneck frequency: how often each operation was a unit's top operation.
    freq: Dict[str, int] = {}
    for row in rows:
        if row.bottleneck_operation:
            freq[row.bottleneck_operation] = freq.get(row.bottleneck_operation, 0) + 1
    most_common, most_common_count = ('', 0)
    for op, count in sorted(freq.items(), key=lambda kv: (-kv[1], kv[0])):
        most_common, most_common_count = op, count
        break
    most_common_pct = (most_common_count / scenarios * 100) if scenarios else 0.0

    # Inter-tick P5 range across scenarios.
    p5s = [r.inter_tick.p5_ms for r in rows if r.inter_tick]
    p5_min_ms = min(p5s) if p5s else 0.0
    p5_max_ms = max(p5s) if p5s else 0.0

    # Budget recommendation: P95 of per-scenario avg/tick processing time, + 10% margin.
    avgs = sorted(r.avg_per_tick_ms for r in rows if r.avg_per_tick_ms > 0)
    if avgs:
        p95_idx = min(int(len(avgs) * 0.95), len(avgs) - 1)
        p95_processing_ms = avgs[p95_idx]
        suggested_budget_ms = round(p95_processing_ms * 1.1, 3)
    else:
        p95_processing_ms, suggested_budget_ms = 0.0, 0.0

    # Cross-scenario average per-operation call time (mean of per-unit avg_time_ms).
    op_sum: Dict[str, float] = {}
    op_cnt: Dict[str, int] = {}
    for row in rows:
        for op in row.operations:
            op_sum[op.operation] = op_sum.get(op.operation, 0.0) + op.avg_time_ms
            op_cnt[op.operation] = op_cnt.get(op.operation, 0) + 1
    avg_operation_times = [
        ProfilingOperationRow(operation=name, avg_time_ms=op_sum[name] / op_cnt[name])
        for name in op_sum
    ]
    avg_operation_times.sort(key=lambda o: o.avg_time_ms, reverse=True)

    # Clipping roll-up (only meaningful when a budget was active).
    clips = [r.clipping for r in rows if r.clipping]
    clipping_total_ticks = sum(c.ticks_total for c in clips)
    clipping_total_kept = sum(c.ticks_kept for c in clips)
    clipping_total_clipped = sum(c.ticks_clipped for c in clips)
    clipping_budgets = sorted({c.budget_ms for c in clips})

    # Per-operation bottleneck frequency (all operations seen). `status` is a display
    # classification only (expected hot-path vs. infra) — the "is this a problem?" verdict is a
    # decision and lives in the post-run validator (#395, no decisions in reports).
    all_ops = {op.operation for row in rows for op in row.operations}
    bottlenecks = [
        ProfilingBottleneckRow(
            operation=op,
            scenario_count=freq.get(op, 0),
            total_scenarios=scenarios,
            pct=(freq.get(op, 0) / scenarios * 100) if scenarios else 0.0,
            status=_bottleneck_status(op, freq.get(op, 0)))
        for op in all_ops
    ]
    bottlenecks.sort(key=lambda b: (-b.scenario_count, b.operation))

    return ProfilingAggregate(
        scenarios=scenarios, total_ticks=total_ticks, total_time_s=total_time_ms / 1000,
        avg_per_tick_ms=avg_per_tick_ms, most_common_bottleneck=most_common,
        most_common_bottleneck_pct=most_common_pct, p5_min_ms=p5_min_ms, p5_max_ms=p5_max_ms,
        p95_processing_ms=p95_processing_ms, suggested_budget_ms=suggested_budget_ms,
        budget_active=budget_active, clipping_total_ticks=clipping_total_ticks,
        clipping_total_kept=clipping_total_kept, clipping_total_clipped=clipping_total_clipped,
        clipping_budgets=clipping_budgets, avg_operation_times=avg_operation_times,
        bottlenecks=bottlenecks)


def _bottleneck_status(operation: str, count: int) -> str:
    """
    Display classification only: 'expected' (intended hot path) vs 'infra', or 'none' when this
    operation was never the bottleneck. NOT a verdict — whether an infra bottleneck is a problem
    is decided by the post-run validator (#395).
    """
    if count == 0:
        return 'none'
    return 'expected' if operation in EXPECTED_OPERATIONS else 'infra'
