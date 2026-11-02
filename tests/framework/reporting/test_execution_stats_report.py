"""
Execution-Stats Report Builder Tests (#391).

Counts-only segment: order counts + SL/TP triggers, currency-agnostic. Tested against
a real BatchExecutionSummary / ProcessResult / ProcessTickLoopResult / SingleScenario
(not stand-ins), extracted into RunUnits — so it exercises the actual persist-path
attribute access (the symbol comes from the index-synced SingleScenario, since
ProcessResult carries none). The live builder uses a real AutoTraderResult.
"""

from datetime import datetime, timezone

import pytest

from python.framework.reporting.builders.execution_stats_report_builder import (
    build_execution_stats_report,
)
from python.framework.reporting.builders.run_unit import (
    RunUnit,
    run_units_from_batch,
    run_units_from_session,
)
from python.framework.types.autotrader_types.autotrader_result_types import AutoTraderResult
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.fill_slippage_types import SlippageReference
from python.framework.types.process_data_types import ProcessResult, ProcessTickLoopResult
from python.framework.types.scenario_types.scenario_set_types import SingleScenario
from python.framework.types.trading_env_types.order_event_types import OrderEvent, OrderEventType
from python.framework.types.trading_env_types.order_types import (
    FAILED_ORDER_STATUSES,
    OrderAction,
    OrderDirection,
    OrderType,
)
from python.framework.types.trading_env_types.trading_env_stats_types import (
    EXECUTION_STATS_FIELD_BY_STATUS,
    ExecutionStats,
)

# Every report artifact names its run (#475); the value is opaque to these tests.
_RUN_ID = '20260830_120000_a1b2c3d4'

_DT = datetime(2025, 10, 13, tzinfo=timezone.utc)


def _stats(sent: int = 5, executed: int = 4, rejected: int = 1, sl_tp: int = 2) -> ExecutionStats:
    """A real counts-only ExecutionStats fixture."""
    return ExecutionStats(
        orders_submitted=sent, orders_executed=executed,
        orders_rejected=rejected, sl_tp_triggered=sl_tp)


def _process_result(name: str, idx: int, stats: ExecutionStats) -> ProcessResult:
    return ProcessResult(
        success=True, scenario_name=name, scenario_index=idx,
        tick_loop_results=ProcessTickLoopResult(execution_stats=stats))


def _scenario(name: str, idx: int, symbol: str) -> SingleScenario:
    return SingleScenario(
        name=name, scenario_index=idx, symbol=symbol,
        data_broker_type='mt5', start_date=_DT)


def _batch(extra_results=None) -> BatchExecutionSummary:
    """A real two-scenario batch with per-scenario execution stats."""
    results = [
        _process_result('s1', 0, _stats(sent=5, executed=4, rejected=1, sl_tp=2)),
        _process_result('s2', 1, _stats(sent=3, executed=3, rejected=0, sl_tp=1)),
    ]
    if extra_results:
        results.extend(extra_results)
    scenarios = [_scenario('s1', 0, 'EURUSD'), _scenario('s2', 1, 'GBPUSD')]
    return BatchExecutionSummary(
        batch_execution_time=0.0, batch_warmup_time=0.0, batch_tickrun_time=0.0,
        process_result_list=results, single_scenario_list=scenarios)


class TestBatch:
    """sim: N scenario units (symbol from SingleScenario) + summed totals."""

    def test_units_use_scenario_symbol(self):
        report = build_execution_stats_report(_RUN_ID, run_units_from_batch(_batch()))
        assert [u.name for u in report.units] == ['s1', 's2']
        # symbol is NOT on ProcessResult — must resolve via the index-synced scenario
        assert [u.symbol for u in report.units] == ['EURUSD', 'GBPUSD']

    def test_unit_counts_mapped(self):
        report = build_execution_stats_report(_RUN_ID, run_units_from_batch(_batch()))
        row = report.units[0]
        assert (row.orders_submitted, row.orders_executed, row.orders_rejected, row.sl_tp_triggered) \
            == (5, 4, 1, 2)

    def test_totals_sum_currency_agnostic(self):
        report = build_execution_stats_report(_RUN_ID, run_units_from_batch(_batch()))
        assert report.totals.orders_submitted == 8
        assert report.totals.orders_executed == 7
        assert report.totals.orders_rejected == 1
        assert report.totals.sl_tp_triggered == 3

    def test_skips_scenarios_without_stats(self):
        bad = ProcessResult(
            success=False, scenario_name='bad', scenario_index=2, tick_loop_results=None)
        report = build_execution_stats_report(_RUN_ID, run_units_from_batch(_batch(extra_results=[bad])))
        assert [u.name for u in report.units] == ['s1', 's2']


class TestSession:
    """live: 1 unit = its own totals."""

    def test_single_unit_and_totals(self):
        result = AutoTraderResult(
            execution_stats=_stats(sent=7, executed=6, rejected=1, sl_tp=4))
        report = build_execution_stats_report(_RUN_ID, 
            run_units_from_session(result, 'my_profile', 'BTCUSD'))
        assert len(report.units) == 1
        assert report.units[0].name == 'my_profile'
        assert report.units[0].symbol == 'BTCUSD'
        assert report.totals.orders_executed == 6
        assert report.totals.sl_tp_triggered == 4

    def test_empty_when_no_stats(self):
        report = build_execution_stats_report(_RUN_ID, 
            run_units_from_session(AutoTraderResult(execution_stats=None), 'p', 'BTCUSD'))
        assert report.units == []
        assert report.totals.orders_submitted == 0


class TestTheFailedCount:
    """
    `orders_failed` is the declared failure category — denied, rejected, undelivered,
    unaccounted — summed from the counts, so it holds where the order history is capped.
    """

    def test_every_failed_status_has_a_count(self):
        assert all(EXECUTION_STATS_FIELD_BY_STATUS[status] for status in FAILED_ORDER_STATUSES)

    def test_it_sums_exactly_the_declared_statuses(self):
        stats = ExecutionStats(orders_submitted=20, orders_executed=9, orders_denied=1,
                               orders_rejected=2, orders_cancelled=3, orders_expired=4,
                               orders_undelivered=5, orders_unaccounted=6)

        row = build_execution_stats_report(_RUN_ID, run_units_from_session(
            AutoTraderResult(execution_stats=stats), 'session', 'BTCUSD')).units[0]

        assert row.orders_failed == 1 + 2 + 5 + 6, 'cancelled and expired are endings, not failures'

    def test_the_totals_add_the_units_up(self):
        report = build_execution_stats_report(_RUN_ID, run_units_from_batch(_batch()))

        assert report.totals.orders_failed == sum(u.orders_failed for u in report.units) == 1


def _buys(market_pcts=(), limit_pcts=(), unmeasured=0):
    """
    A unit's order events: one buy per figure, each filled that many percent above its reference
    of 100 — market orders against their mid, limits against their limit.

    Args:
        market_pcts: The market fills' slippage in percent
        limit_pcts: The limit fills' slippage in percent
        unmeasured: Market fills sent without a mid

    Returns:
        The events
    """
    events, seq = [], 0
    orders = ([(OrderType.MARKET, pct) for pct in market_pcts]
              + [(OrderType.LIMIT, pct) for pct in limit_pcts]
              + [(OrderType.MARKET, None)] * unmeasured)
    for order_type, pct in orders:
        submitted = seq + 1
        prices = ({'limit_price': 100.0} if order_type is OrderType.LIMIT
                  else {'submission_mid': 100.0 if pct is not None else None})
        common = dict(order_id=f'o{submitted}', submitted_seq=submitted, order_type=order_type,
                      direction=OrderDirection.LONG, action=OrderAction.OPEN, **prices)
        events.append(OrderEvent(seq=submitted, event_type=OrderEventType.SUBMITTED, **common))
        events.append(OrderEvent(seq=submitted + 1, event_type=OrderEventType.FILLED,
                                 fill_price=100.0 + (pct or 0.0), **common))
        seq += 2
    return events


class TestFillSlippage:
    """#631: how far the fills landed, per unit and reference kind, and over the whole run."""

    def _report(self):
        return build_execution_stats_report(_RUN_ID, [
            RunUnit(name='scenario_a', symbol='BTCUSD',
                    order_events=_buys(market_pcts=(0.01, 0.03, 0.05), limit_pcts=(-0.02,))),
            RunUnit(name='scenario_b', symbol='ETHUSD', order_events=_buys(market_pcts=(0.10,))),
        ])

    def test_one_row_per_unit_and_reference_kind(self):
        rows = {(row.name, row.measured_against): row for row in self._report().fill_slippage}

        assert set(rows) == {('scenario_a', SlippageReference.SUBMISSION_MID),
                             ('scenario_a', SlippageReference.LIMIT_PRICE),
                             ('scenario_b', SlippageReference.SUBMISSION_MID)}
        market_a = rows[('scenario_a', SlippageReference.SUBMISSION_MID)]
        assert (market_a.fills, market_a.fills_measured) == (3, 3)
        assert market_a.adverse_pct_mean == pytest.approx(0.03)
        assert market_a.adverse_pct_max == pytest.approx(0.05)
        assert rows[('scenario_a', SlippageReference.LIMIT_PRICE)].adverse_pct_mean == (
            pytest.approx(-0.02)), 'a limit fill is never averaged with a market fill'

    def test_the_run_figure_is_derived_over_every_fill_never_a_mean_of_means(self):
        totals = {row.measured_against: row for row in self._report().fill_slippage_totals}

        market = totals[SlippageReference.SUBMISSION_MID]
        assert market.fills == 4
        assert market.adverse_pct_mean == pytest.approx((0.01 + 0.03 + 0.05 + 0.10) / 4)
        assert market.adverse_pct_mean != pytest.approx((0.03 + 0.10) / 2)
        assert market.adverse_pct_max == pytest.approx(0.10)

    def test_a_fill_without_its_reference_is_counted_and_not_measured(self):
        report = build_execution_stats_report(_RUN_ID, [
            RunUnit(name='btc_session', symbol='BTCUSD', order_events=_buys(unmeasured=2))])

        row = report.fill_slippage[0]
        assert (row.fills, row.fills_measured) == (2, 0)
        assert (row.adverse_pct_mean, row.adverse_pct_max) == (None, None)

    def test_a_unit_without_fills_has_no_row(self):
        report = build_execution_stats_report(_RUN_ID, [RunUnit(name='idle', symbol='BTCUSD')])

        assert report.fill_slippage == [] and report.fill_slippage_totals == []

    def test_the_lists_declare_what_makes_a_row_unique(self):
        assert self._report().keys == {
            'units': ['name'], 'fill_slippage': ['name', 'measured_against'],
            'fill_slippage_totals': ['measured_against']}
