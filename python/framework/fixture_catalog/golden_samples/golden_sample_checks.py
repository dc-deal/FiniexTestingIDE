"""
Golden sample checks (#557) — every aggregate a frozen sample can rebuild, and the comparison.

Each block names one aggregate the reports serve, which sections of a run it is computed from,
how today's code rebuilds it from them, and where the run recorded it. The read-time views — the
run list's figures, the deployments and the sweeps — are composed from the frozen ledger rows and
run index rows the same way the routes compose them from the stores. The freezer runs these
checks before it writes a sample, the suite runs them on every committed one, and both compare
field by field: a float equal to the last bit, a missing field a difference like any other.

What is frozen and cannot be rebuilt says why in `FROZEN_NOT_REBUILT`.
"""

from datetime import datetime
from typing import Any, Dict, FrozenSet, List, Optional, Sequence, Tuple

from pydantic import BaseModel

from python.framework.optimization.optimization_analysis import (
    compose_sweep_detail,
    compose_sweep_list,
)
from python.framework.reporting.aggregation.aggregation_fold import fold_group, fold_rows
from python.framework.reporting.aggregation.tables.booking_figure_table import (
    BOOKING_RUN_TOTALS_TABLE,
    BOOKING_UNIT_CLOSE_TABLE,
)
from python.framework.reporting.aggregation.tables.feed_stability_figure_table import (
    FEED_COUNTER_TABLE,
    FEED_SOURCE_TABLE,
    FEED_TOTALS_TABLE,
)
from python.framework.reporting.builders.aggregated_portfolio_report_builder import (
    build_aggregated_portfolio_report,
)
from python.framework.reporting.builders.booking_periods_report_builder import (
    unit_totals_from_ledger_rows,
)
from python.framework.reporting.builders.deployment_history_builder import (
    compose_deployment_booking_periods,
    compose_deployment_detail,
    compose_deployment_list,
)
from python.framework.reporting.builders.execution_stats_report_builder import slippage_summaries
from python.framework.reporting.builders.profiling_report_builder import with_warmup_shares
from python.framework.reporting.builders.report_aggregators import (
    aggregate_decision_totals,
    aggregate_execution_totals,
    aggregate_failed_orders,
    aggregate_portfolio_by_currency,
    aggregate_profiling,
    aggregate_trade_analytics,
    aggregate_trade_scenario_totals,
    aggregate_worker_decision_bottlenecks,
    aggregate_worker_totals,
)
from python.framework.reporting.builders.run_summary_builder import build_run_summary
from python.framework.reporting.builders.run_unit import RunUnit
from python.framework.reporting.builders.worker_decision_breakdown_builder import (
    build_worker_decision_breakdowns,
)
from python.framework.reporting.io.artifact_specs import (
    AGGREGATED_PORTFOLIO_ARTIFACT,
    BOOKING_PERIODS_ARTIFACT,
    EXECUTION_STATS_ARTIFACT,
    FEED_STABILITY_ARTIFACT,
    ORDER_HISTORY_ARTIFACT,
    PENDING_ORDERS_ARTIFACT,
    PORTFOLIO_ARTIFACT,
    PROFILING_ARTIFACT,
    RUN_SUMMARY_ARTIFACT,
    SCENARIO_DETAILS_ARTIFACT,
    SIGNAL_ARTIFACT,
    TRADE_HISTORY_ARTIFACT,
    WORKER_DECISION_ARTIFACT,
)
from python.framework.reporting.io.report_artifact_io import ArtifactSpec
from python.framework.reporting.store.run_list_figures import fold_run_list_figures
from python.framework.types.api.report_types import (
    FeedStabilitySourceRow,
    FillSlippageRow,
    ProfilingReport,
    RunInfo,
    RunResultRow,
    UnitRoster,
    WarmupPhaseRow,
)
from python.framework.types.disturbance_episode_types import FeedEpisodeInSource
from python.framework.types.fill_slippage_types import FillSlippage
from python.framework.types.golden_sample_types import (
    FrozenRun,
    GoldenSample,
    GoldenSampleBlock,
    GoldenSampleDifference,
    GoldenSampleViews,
)

# The report sections a run's sample freezes, each under the FrozenRun field that holds it. A test
# holds this to FrozenRun's section fields, both ways.
FROZEN_SECTIONS: Tuple[Tuple[str, ArtifactSpec], ...] = (
    ('portfolio', PORTFOLIO_ARTIFACT),
    ('trade_history', TRADE_HISTORY_ARTIFACT),
    ('order_history', ORDER_HISTORY_ARTIFACT),
    ('execution_stats', EXECUTION_STATS_ARTIFACT),
    ('pending_orders', PENDING_ORDERS_ARTIFACT),
    ('worker_decision', WORKER_DECISION_ARTIFACT),
    ('profiling', PROFILING_ARTIFACT),
    ('signal', SIGNAL_ARTIFACT),
    ('feed_stability', FEED_STABILITY_ARTIFACT),
    ('booking_periods', BOOKING_PERIODS_ARTIFACT),
    ('aggregated_portfolio', AGGREGATED_PORTFOLIO_ARTIFACT),
    ('run_summary', RUN_SUMMARY_ARTIFACT),
    ('scenario_details', SCENARIO_DETAILS_ARTIFACT),
)

# Frozen, and not rebuilt — each is folded from inputs that exist only while a run runs, so
# nothing the run persists can feed it again.
FROZEN_NOT_REBUILT: Dict[str, str] = {
    'signal': 'merged from per-worker counters and per-symbol coverages that exist only while '
              'the run runs; its folds are declared in the signal tables, and checked by '
              'producing the entry again',
    'feed-stability.counters': "joined onto the source rows from the units' tick and signal "
                               'counters, which exist only while the run runs; block J leaves '
                               'them out on both sides',
}

# The session-span fields of the run summary: a backtest's spans are in its scenario details, an
# AutoTrader session's only in the summary itself.
_SPAN_FIELDS = frozenset({'tick_timespan_seconds', 'tick_timespan_total_seconds'})


# ============================================
# The blocks — one aggregate each
# ============================================

def _rebuild_portfolio_headline(run: FrozenRun) -> Any:
    """
    Block A — the portfolio headline per account currency, from the unit rows.

    Args:
        run: The frozen run

    Returns:
        The rebuilt aggregates
    """
    return aggregate_portfolio_by_currency(run.portfolio.units)


def _recorded_portfolio_headline(run: FrozenRun) -> Any:
    """
    Block A as the run recorded it.

    Args:
        run: The frozen run

    Returns:
        The recorded aggregates
    """
    return run.portfolio.aggregates


def _rebuild_run_summary(run: FrozenRun) -> Any:
    """
    Block B — the run summary, composed from the frozen sections.

    The roster is read back from the recorded summary: the pipeline built it from its batch,
    which no run persists, and the summary is where its three counts were written.

    Args:
        run: The frozen run

    Returns:
        The rebuilt summary
    """
    recorded = run.run_summary
    roster = UnitRoster(declared=recorded.units_declared, disabled=recorded.units_disabled,
                        absent=recorded.units_absent)
    return build_run_summary(run.run_id, run.portfolio, run.trade_history, run.execution_stats,
                             run.signal, run.feed_stability, roster, _span_units(run))


def _recorded_run_summary(run: FrozenRun) -> Any:
    """
    Block B as the run recorded it.

    Args:
        run: The frozen run

    Returns:
        The recorded summary
    """
    return run.run_summary


def _run_summary_left_out(run: FrozenRun) -> FrozenSet[str]:
    """
    What the run summary's rebuild cannot recompute for this run.

    Args:
        run: The frozen run

    Returns:
        The span fields when the run has no scenario details — an AutoTrader session persists its
        span only as these two results
    """
    return frozenset() if run.scenario_details is not None else _SPAN_FIELDS


def _span_units(run: FrozenRun) -> Optional[List[RunUnit]]:
    """
    The units' processed spans, rebuilt from the scenario details.

    Args:
        run: The frozen run

    Returns:
        One unit per scenario with both ends of its span, or None without scenario details
    """
    if run.scenario_details is None:
        return None
    return [RunUnit(name=row.name, symbol=row.symbol,
                    first_tick_time=datetime.fromisoformat(row.first_tick_time),
                    last_tick_time=datetime.fromisoformat(row.last_tick_time))
            for row in run.scenario_details.units if row.first_tick_time and row.last_tick_time]


def _rebuild_aggregated_portfolio(run: FrozenRun) -> Any:
    """
    Block C — the aggregated portfolio, from the portfolio, execution and pending sections.

    Args:
        run: The frozen run

    Returns:
        The rebuilt report
    """
    return build_aggregated_portfolio_report(run.run_id, run.portfolio, run.execution_stats,
                                             run.pending_orders)


def _recorded_aggregated_portfolio(run: FrozenRun) -> Any:
    """
    Block C as the run recorded it.

    Args:
        run: The frozen run

    Returns:
        The recorded report
    """
    return run.aggregated_portfolio


def _rebuild_trade_analytics(run: FrozenRun) -> Any:
    """
    Block D — the trade analytics per account currency, from the trade rows.

    Args:
        run: The frozen run

    Returns:
        The rebuilt analytics
    """
    return aggregate_trade_analytics(run.trade_history.trades)


def _recorded_trade_analytics(run: FrozenRun) -> Any:
    """
    Block D as the run recorded it.

    Args:
        run: The frozen run

    Returns:
        The recorded analytics
    """
    return run.trade_history.analytics


def _rebuild_trade_scenario_totals(run: FrozenRun) -> Any:
    """
    Block E — the per-scenario trade totals, from the trade rows.

    Which units read input without a quote comes from the portfolio's unit rows, whose spread is
    None exactly for those — a source apart from the totals being checked.

    Args:
        run: The frozen run

    Returns:
        The rebuilt totals
    """
    without_quote = {row.name for row in run.portfolio.units if row.total_spread_cost is None}
    return aggregate_trade_scenario_totals(run.trade_history.trades, without_quote)


def _recorded_trade_scenario_totals(run: FrozenRun) -> Any:
    """
    Block E as the run recorded it.

    Args:
        run: The frozen run

    Returns:
        The recorded totals
    """
    return run.trade_history.scenario_totals


def _rebuild_execution_totals(run: FrozenRun) -> Any:
    """
    Block F — the execution totals, from the unit rows.

    Args:
        run: The frozen run

    Returns:
        The rebuilt totals
    """
    return aggregate_execution_totals(run.execution_stats.units)


def _recorded_execution_totals(run: FrozenRun) -> Any:
    """
    Block F as the run recorded it.

    Args:
        run: The frozen run

    Returns:
        The recorded totals
    """
    return run.execution_stats.totals


def _rebuild_fill_slippage(run: FrozenRun) -> Any:
    """
    Block F's slippage — per unit and in total, from the order-history rows of the fills.

    The run derived each fill from its order-event stream, which carries a protective close's
    level only in memory; every fill's result is booked on its order-history row, keyed by the
    event it came from, and taken in that order the folds see the fills as the run did.

    Args:
        run: The frozen run

    Returns:
        The rebuilt per-unit rows and totals
    """
    fills_by_unit: Dict[str, List[Tuple[int, FillSlippage]]] = {}
    for row in run.order_history.orders:
        if row.slippage_measured_against is None:
            continue
        fill = FillSlippage(measured_against=row.slippage_measured_against,
                            reference_price=row.slippage_reference_price,
                            slippage=row.slippage, slippage_pct=row.slippage_pct)
        fills_by_unit.setdefault(row.scenario_name, []).append((row.order_event_seq or 0, fill))
    unit_order = [row.name for row in run.execution_stats.units]
    names = unit_order + sorted(name for name in fills_by_unit if name not in unit_order)
    ordered = {name: [fill for _, fill in sorted(fills_by_unit[name], key=lambda pair: pair[0])]
               for name in names if name in fills_by_unit}
    return {
        'fill_slippage': [FillSlippageRow(name=name, **summary.model_dump())
                          for name, fills in ordered.items()
                          for summary in slippage_summaries(fills)],
        'fill_slippage_totals': slippage_summaries(
            [fill for fills in ordered.values() for fill in fills]),
    }


def _recorded_fill_slippage(run: FrozenRun) -> Any:
    """
    Block F's slippage as the run recorded it.

    Args:
        run: The frozen run

    Returns:
        The recorded per-unit rows and totals
    """
    return {'fill_slippage': run.execution_stats.fill_slippage,
            'fill_slippage_totals': run.execution_stats.fill_slippage_totals}


def _rebuild_worker_totals(run: FrozenRun) -> Any:
    """
    Block H — the worker totals, from the unit rows.

    Args:
        run: The frozen run

    Returns:
        The rebuilt totals
    """
    return aggregate_worker_totals(run.worker_decision.units)


def _recorded_worker_totals(run: FrozenRun) -> Any:
    """
    Block H as the run recorded it.

    Args:
        run: The frozen run

    Returns:
        The recorded totals
    """
    return run.worker_decision.worker_totals


def _tracked_units(run: FrozenRun) -> list:
    """
    The worker/decision unit rows that tracked their decisions — what the totals fold over.

    Args:
        run: The frozen run

    Returns:
        The tracked unit rows
    """
    return [unit for unit in run.worker_decision.units if unit.worker_decision_tracked]


def _rebuild_decision_totals(run: FrozenRun) -> Any:
    """
    Block M — the decision logic's totals, from the tracked unit rows.

    Args:
        run: The frozen run

    Returns:
        The rebuilt totals
    """
    return aggregate_decision_totals(_tracked_units(run))


def _recorded_decision_totals(run: FrozenRun) -> Any:
    """
    Block M as the run recorded it.

    Args:
        run: The frozen run

    Returns:
        The recorded totals
    """
    return run.worker_decision.decision_totals


def _rebuild_bottlenecks(run: FrozenRun) -> Any:
    """
    Block N — the slowest performers, from the tracked unit rows.

    Args:
        run: The frozen run

    Returns:
        The rebuilt bottlenecks
    """
    return aggregate_worker_decision_bottlenecks(_tracked_units(run))


def _recorded_bottlenecks(run: FrozenRun) -> Any:
    """
    Block N as the run recorded it.

    Args:
        run: The frozen run

    Returns:
        The recorded bottlenecks
    """
    return run.worker_decision.bottlenecks


def _rebuild_failed_orders(run: FrozenRun) -> Any:
    """
    Block O — the failed orders counted, from the frozen order rows.

    Args:
        run: The frozen run

    Returns:
        The rebuilt counts
    """
    return aggregate_failed_orders(run.order_history.orders)


def _recorded_failed_orders(run: FrozenRun) -> Any:
    """
    Block O as the run recorded it.

    Args:
        run: The frozen run

    Returns:
        The recorded counts
    """
    report = run.order_history
    return {'failed_listed': report.failed_listed, 'failed_by_unit': report.failed_by_unit,
            'failed_by_reason': report.failed_by_reason}


def _rebuild_warmup(run: FrozenRun) -> Any:
    """
    Block P — the warmup's total, slowest phase and shares, from the phases' own durations.

    Args:
        run: The frozen run

    Returns:
        The rebuilt warmup part
    """
    report = with_warmup_shares(ProfilingReport(
        run_id=run.run_id, units=[],
        warmup_phases=[WarmupPhaseRow(name=phase.name, duration_s=phase.duration_s)
                       for phase in run.profiling.warmup_phases]))
    return _warmup_view(report)


def _recorded_warmup(run: FrozenRun) -> Any:
    """
    Block P as the run recorded it.

    Args:
        run: The frozen run

    Returns:
        The recorded warmup part
    """
    return _warmup_view(run.profiling)


def _warmup_view(report: ProfilingReport) -> Dict[str, Any]:
    """
    The warmup as block P compares it — the phases with their shares, the whole and its slowest.

    Args:
        report: A profiling report

    Returns:
        The comparable view
    """
    return {'warmup_phases': report.warmup_phases, 'warmup_total_s': report.warmup_total_s,
            'warmup_slowest_phase': report.warmup_slowest_phase,
            'warmup_slowest_s': report.warmup_slowest_s,
            'warmup_slowest_pct': report.warmup_slowest_pct}


def _rebuild_breakdowns(run: FrozenRun) -> Any:
    """
    Block Q — where each unit's worker_decision time went, from the two frozen sections.

    Args:
        run: The frozen run

    Returns:
        The rebuilt breakdowns
    """
    return build_worker_decision_breakdowns(run.profiling, run.worker_decision)


def _recorded_breakdowns(run: FrozenRun) -> Any:
    """
    Block Q as the run recorded it.

    Args:
        run: The frozen run

    Returns:
        The recorded breakdowns
    """
    return run.profiling.worker_decision_breakdowns


def _rebuild_profiling(run: FrozenRun) -> Any:
    """
    Block I — the profiling aggregate, from the unit rows.

    Whether a tick budget was active is read from the recorded aggregate: the run decided it from
    its batch, and the aggregate is where it wrote it down. The timings themselves are the frozen
    rows' — nothing is measured again.

    Args:
        run: The frozen run

    Returns:
        The rebuilt aggregate
    """
    return aggregate_profiling(run.profiling.units, run.profiling.aggregate.budget_active)


def _recorded_profiling(run: FrozenRun) -> Any:
    """
    Block I as the run recorded it.

    Args:
        run: The frozen run

    Returns:
        The recorded aggregate
    """
    return run.profiling.aggregate


def _rebuild_booking_unit_totals(run: FrozenRun) -> Any:
    """
    Block L — one total per unit, from the run's period rows in the LEDGER.

    The report built its totals from the sealed periods in memory; the ledger holds the same
    periods as rows. Rebuilt from those, the check also says that the two stores agree.

    Args:
        run: The frozen run

    Returns:
        The rebuilt unit totals
    """
    currency = run.booking_periods.currency
    rows = [row for row in run.ledger_rows if row.currency == currency and row.period_opened_at]
    return unit_totals_from_ledger_rows(rows)


def _recorded_booking_unit_totals(run: FrozenRun) -> Any:
    """
    Block L as the run recorded it.

    Args:
        run: The frozen run

    Returns:
        The recorded unit totals
    """
    return run.booking_periods.unit_totals


# A source row's counters exist only while a run runs, and so does the share read from them
_FROM_THE_COUNTERS = frozenset({*FEED_COUNTER_TABLE.figures, 'fresh_tick_pct'})


def _feed_view(units: Sequence[FeedStabilitySourceRow], totals: Dict[str, Any]) -> Dict[str, Any]:
    """
    The feed stability as block J compares it — the source rows without their counters, and the
    totals over them.

    Args:
        units: The source rows
        totals: The report's totals

    Returns:
        The comparable view
    """
    return {'units': [{name: value for name, value in unit.model_dump().items()
                       if name not in _FROM_THE_COUNTERS} for unit in units],
            **totals}


def _rebuild_feed_stability(run: FrozenRun) -> Any:
    """
    Block J — the feed stability's source rows and totals, from the frozen episodes.

    The recorded rows are already in the served order, so they are fed in reverse — each row's own
    episodes kept in their order, because the stale seconds add in it. Only the fold's group order
    can then put them back, and a change to it shows here.

    Args:
        run: The frozen run

    Returns:
        The rebuilt view
    """
    episodes = [FeedEpisodeInSource(
        source=row.source, domain=row.domain, unit=episode.unit, symbol=episode.symbol,
        stale_from=episode.stale_from, stale_to=episode.stale_to,
        duration_seconds=episode.duration_seconds, origin=episode.origin, label=episode.label)
        for row in reversed(run.feed_stability.units) for episode in row.episodes]
    rows = [FeedStabilitySourceRow(**figures) for figures in fold_rows(episodes, FEED_SOURCE_TABLE)]
    return _feed_view(rows, fold_group(rows, FEED_TOTALS_TABLE))


def _recorded_feed_stability(run: FrozenRun) -> Any:
    """
    Block J — the feed stability as the run recorded it.

    Args:
        run: The frozen run

    Returns:
        The recorded view
    """
    report = run.feed_stability
    return _feed_view(report.units,
                      {name: getattr(report, name) for name in FEED_TOTALS_TABLE.figures})


def _feed_stability_left_out(run: FrozenRun) -> FrozenSet[str]:
    """
    A run without a disturbance serves no totals, only the report's defaults.

    Args:
        run: The frozen run

    Returns:
        The totals for a run without a source row, else nothing
    """
    return frozenset() if run.feed_stability.units else frozenset(FEED_TOTALS_TABLE.figures)


_BOOKING_RUN_TOTALS = (*BOOKING_RUN_TOTALS_TABLE.figures, *BOOKING_UNIT_CLOSE_TABLE.figures)


def _rebuild_booking_run_totals(run: FrozenRun) -> Any:
    """
    Block K — the booking periods' run totals, from the frozen periods and unit totals.

    Args:
        run: The frozen run

    Returns:
        The rebuilt totals
    """
    report = run.booking_periods
    return {**fold_group(report.periods, BOOKING_RUN_TOTALS_TABLE),
            **fold_group(report.unit_totals, BOOKING_UNIT_CLOSE_TABLE)}


def _recorded_booking_run_totals(run: FrozenRun) -> Any:
    """
    Block K — the run totals as the run recorded them.

    Args:
        run: The frozen run

    Returns:
        The recorded totals
    """
    return {name: getattr(run.booking_periods, name) for name in _BOOKING_RUN_TOTALS}


def _booking_run_totals_left_out(run: FrozenRun) -> FrozenSet[str]:
    """
    A run that booked no period in its reported currency serves no totals, only the defaults.

    Args:
        run: The frozen run

    Returns:
        Every total for a run without a period, else nothing
    """
    return frozenset() if run.booking_periods.periods else frozenset(_BOOKING_RUN_TOTALS)


GOLDEN_SAMPLE_BLOCKS: Tuple[GoldenSampleBlock, ...] = (
    GoldenSampleBlock('portfolio.aggregates', 'A · the portfolio headline per account currency',
                      ('portfolio',), _rebuild_portfolio_headline, _recorded_portfolio_headline),
    GoldenSampleBlock('run-summary', 'B · the run summary',
                      ('portfolio', 'trade_history', 'execution_stats', 'run_summary'),
                      _rebuild_run_summary, _recorded_run_summary, _run_summary_left_out),
    GoldenSampleBlock('aggregated-portfolio', 'C · the aggregated portfolio',
                      ('portfolio', 'execution_stats', 'pending_orders', 'aggregated_portfolio'),
                      _rebuild_aggregated_portfolio, _recorded_aggregated_portfolio),
    GoldenSampleBlock('trade-history.analytics', 'D · the trade analytics per account currency',
                      ('trade_history',), _rebuild_trade_analytics, _recorded_trade_analytics),
    GoldenSampleBlock('trade-history.scenario_totals', 'E · the trade totals per scenario',
                      ('trade_history', 'portfolio'), _rebuild_trade_scenario_totals,
                      _recorded_trade_scenario_totals),
    GoldenSampleBlock('execution-stats.totals', 'F · the execution totals',
                      ('execution_stats',), _rebuild_execution_totals, _recorded_execution_totals),
    GoldenSampleBlock('execution-stats.fill_slippage',
                      'F · the fill slippage per unit and in total',
                      ('execution_stats', 'order_history'), _rebuild_fill_slippage,
                      _recorded_fill_slippage),
    GoldenSampleBlock('worker-decision.worker_totals', 'H · the worker totals',
                      ('worker_decision',), _rebuild_worker_totals, _recorded_worker_totals),
    GoldenSampleBlock('profiling.aggregate', 'I · the profiling aggregate',
                      ('profiling',), _rebuild_profiling, _recorded_profiling),
    GoldenSampleBlock('feed-stability', 'J · the feed stability per source, without its counters',
                      ('feed_stability',), _rebuild_feed_stability, _recorded_feed_stability,
                      _feed_stability_left_out),
    GoldenSampleBlock('booking-periods.run_totals', 'K · the booking periods per run',
                      ('booking_periods',), _rebuild_booking_run_totals,
                      _recorded_booking_run_totals, _booking_run_totals_left_out),
    GoldenSampleBlock('booking-periods.unit_totals', 'L · the booking periods per unit',
                      ('booking_periods',), _rebuild_booking_unit_totals,
                      _recorded_booking_unit_totals),
    GoldenSampleBlock('worker-decision.decision_totals', 'M · the decision totals',
                      ('worker_decision',), _rebuild_decision_totals, _recorded_decision_totals),
    GoldenSampleBlock('worker-decision.bottlenecks', 'N · the slowest performers',
                      ('worker_decision',), _rebuild_bottlenecks, _recorded_bottlenecks),
    GoldenSampleBlock('order-history.failed', 'O · the failed orders counted',
                      ('order_history',), _rebuild_failed_orders, _recorded_failed_orders),
    GoldenSampleBlock('profiling.warmup', 'P · the warmup',
                      ('profiling',), _rebuild_warmup, _recorded_warmup),
    GoldenSampleBlock('profiling.worker_decision_breakdowns', 'Q · the worker/decision breakdown',
                      ('profiling', 'worker_decision'), _rebuild_breakdowns, _recorded_breakdowns),
)


# ============================================
# The read-time views
# ============================================

def compose_views(
    rows: List[RunResultRow],
    runs: List[RunInfo],
    now: datetime,
    run_ids: Sequence[str],
    deployment_ids: Sequence[str],
    sweep_ids: Sequence[str],
) -> GoldenSampleViews:
    """
    The read-time views of one production, composed the way the routes compose them.

    Called on the real stores to freeze what the API serves, and on the frozen rows to check it:
    the routes filter the ledger by deployment or sweep and the run index by parent, and so does
    this, so the two calls see the same rows for every subject.

    Args:
        rows: Ledger rows — every row of the store, or the frozen ones
        runs: Run index rows — every run, or the frozen ones
        now: The instant the deployment code summaries measure against
        run_ids: The production's runs
        deployment_ids: Its deployments
        sweep_ids: Its sweeps

    Returns:
        The views, restricted to the production's subjects
    """
    figures = fold_run_list_figures(rows)
    views = GoldenSampleViews(
        run_list_figures={run_id: figures[run_id] for run_id in run_ids if run_id in figures})
    if deployment_ids:
        listed = compose_deployment_list(rows, runs, now).deployments
        views.deployments = [row for row in listed if row.deployment_id in deployment_ids]
    for deployment_id in deployment_ids:
        own_rows = [row for row in rows if row.deployment_id == deployment_id]
        own_runs = [run for run in runs if run.parent_id == deployment_id]
        detail = compose_deployment_detail(deployment_id, own_rows, own_runs, now)
        if detail is not None:
            views.deployment_details[deployment_id] = detail
        periods = compose_deployment_booking_periods(deployment_id, own_rows, own_runs)
        if periods is not None:
            views.deployment_booking_periods[deployment_id] = periods
    if sweep_ids:
        views.sweeps = [row for row in compose_sweep_list(rows).sweeps if row.sweep_id in sweep_ids]
    for sweep_id in sweep_ids:
        detail = compose_sweep_detail(sweep_id, [row for row in rows if row.sweep_id == sweep_id])
        if detail is not None:
            views.sweep_details[sweep_id] = detail
    return views


# ============================================
# The checks
# ============================================

def block_applies(block: GoldenSampleBlock, run: FrozenRun) -> bool:
    """
    Whether a run carries every section a block is computed from.

    Args:
        block: The block
        run: The frozen run

    Returns:
        True when every section the block needs was frozen for the run
    """
    return all(getattr(run, name) is not None for name in block.needs)


def check_block(sample: GoldenSample, block: GoldenSampleBlock) -> List[GoldenSampleDifference]:
    """
    Rebuild one block for every run it applies to, and compare it with the recorded one.

    Args:
        sample: The golden sample
        block: The block

    Returns:
        Every field the rebuild does not reproduce; empty when all agree
    """
    differences: List[GoldenSampleDifference] = []
    for run in sample.runs:
        if not block_applies(block, run):
            continue
        for path, recorded, rebuilt in json_differences(
                block.recorded(run), block.rebuild(run), block.left_out(run)):
            differences.append(GoldenSampleDifference(
                sample.header.entry_id, block.block_id, run.run_id, path, recorded, rebuilt))
    return differences


def check_views(sample: GoldenSample) -> List[GoldenSampleDifference]:
    """
    Compose the read-time views from the frozen rows, and compare them with the frozen views.

    Args:
        sample: The golden sample

    Returns:
        Every field the composition does not reproduce; empty when all agree
    """
    header = sample.header
    rebuilt = compose_views(sample.inputs.ledger_rows, sample.inputs.run_index, header.composed_at,
                            [run.run_id for run in header.runs], header.deployment_ids,
                            header.sweep_ids)
    return [GoldenSampleDifference(header.entry_id, 'views', header.entry_id, path, recorded, built)
            for path, recorded, built in json_differences(sample.views, rebuilt)]


def check_golden_sample(sample: GoldenSample) -> List[GoldenSampleDifference]:
    """
    Every check a golden sample carries.

    Args:
        sample: The golden sample

    Returns:
        Every field not reproduced, over all blocks and the views
    """
    differences: List[GoldenSampleDifference] = []
    for block in GOLDEN_SAMPLE_BLOCKS:
        differences.extend(check_block(sample, block))
    differences.extend(check_views(sample))
    return differences


def json_differences(
    recorded: Any,
    rebuilt: Any,
    left_out: FrozenSet[str] = frozenset(),
) -> List[Tuple[str, Any, Any]]:
    """
    Compare two aggregates field by field, as the JSON they are served as.

    Args:
        recorded: The recorded aggregate — a model, a list of models or a dict of them
        rebuilt: The rebuilt one, of the same shape
        left_out: Top-level fields not compared

    Returns:
        (path, recorded value, rebuilt value) for every field where the two part
    """
    left = _jsonable(recorded)
    right = _jsonable(rebuilt)
    if left_out and isinstance(left, dict) and isinstance(right, dict):
        left = {key: value for key, value in left.items() if key not in left_out}
        right = {key: value for key, value in right.items() if key not in left_out}
    differences: List[Tuple[str, Any, Any]] = []
    _walk(left, right, '', differences)
    return differences


def _jsonable(value: Any) -> Any:
    """
    A value in the shape it is served in.

    Args:
        value: A model, a list, a dict or a plain value

    Returns:
        Its JSON form
    """
    if isinstance(value, BaseModel):
        return value.model_dump(mode='json')
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _walk(left: Any, right: Any, path: str, out: List[Tuple[str, Any, Any]]) -> None:
    """
    Collect where two JSON values part.

    Args:
        left: The recorded value
        right: The rebuilt value
        path: Where both stand
        out: The differences, appended to
    """
    if isinstance(left, dict) and isinstance(right, dict):
        for key in sorted(set(left) | set(right)):
            if key not in left or key not in right:
                out.append((f'{path}.{key}'.lstrip('.'), left.get(key, '<absent>'),
                            right.get(key, '<absent>')))
                continue
            _walk(left[key], right[key], f'{path}.{key}'.lstrip('.'), out)
        return
    if isinstance(left, list) and isinstance(right, list):
        if len(left) != len(right):
            out.append((f'{path}[len]', len(left), len(right)))
            return
        for index, (a, b) in enumerate(zip(left, right)):
            _walk(a, b, f'{path}[{index}]', out)
        return
    if left != right and not (_is_nan(left) and _is_nan(right)):
        out.append((path or '<root>', left, right))


def _is_nan(value: Any) -> bool:
    """
    Whether a value is a float that is not a number — equal to another one for this comparison.

    Args:
        value: Any value

    Returns:
        True for a NaN float
    """
    return isinstance(value, float) and value != value
