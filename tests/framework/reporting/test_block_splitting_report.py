"""
Block-Splitting Report Builder Tests.

`build_block_splitting_report_from_batch` derives each block's edge facts from the raw material
the tick loop hands over — the positions the edge left OPEN, the trades that closed naturally,
the discarded pending orders — and aggregates them per symbol into disposition facts + ratios.
Tested with REAL BatchExecutionSummary / ProcessResult / Position / TradeRecord fixtures (the
generator-mode lookup is a trivial dict, tested via the 'unknown' fallback).
"""
from datetime import datetime, timezone

from python.framework.reporting.builders.block_splitting_report_builder import (
    build_block_splitting_report_from_batch,
)
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.process_data_types import ProcessResult, ProcessTickLoopResult
from python.framework.types.scenario_types.scenario_set_types import SingleScenario
from python.framework.types.trading_env_types.pending_order_stats_types import PendingOrderStats
from tests.shared.fixture_helpers import make_closed_trades, make_open_positions

# Every report artifact names its run (#475); the value is opaque to these tests.
_RUN_ID = '20260830_120000_a1b2c3d4'

# A scenario needs a start; nothing here asserts on it.
_T0 = datetime(2026, 8, 30, 12, 0, tzinfo=timezone.utc)

# scenario name → is_profile_run, filled by _result and read back by _batch. The flag lives on
# the SCENARIO, not on the result, so the fixture has to carry it across the two builders.
_PROFILE_RUN = {}


def _result(name, open_trades=0, open_pnl=0.0, nat_trades=0, nat_pnl=0.0,
            discarded=0, success=True, idx=0, profile_run=True) -> ProcessResult:
    """
    One finished block, as the subprocess hands it back.

    Args:
        name: Its scenario name — the symbol is read off it
        open_trades: Positions the block edge left open
        open_pnl: Unrealised P&L riding on them
        nat_trades: Trades the strategy closed itself
        nat_pnl: Realised P&L from those
        discarded: Pending orders discarded at the edge
        success: False for a scenario that failed
        idx: Its scenario index
        profile_run: False for a normal run, which carries no disposition

    Returns:
        The ProcessResult
    """
    _PROFILE_RUN[name] = profile_run
    pending = PendingOrderStats(total_force_closed=discarded) if discarded else None
    return ProcessResult(
        success=success, scenario_name=name, scenario_index=idx,
        tick_loop_results=ProcessTickLoopResult(
            open_positions=make_open_positions(open_trades, open_pnl),
            trade_history=make_closed_trades(nat_trades, nat_pnl),
            pending_stats=pending))


def _batch(results) -> BatchExecutionSummary:
    scenarios = [
        SingleScenario(
            name=r.scenario_name, scenario_index=r.scenario_index, symbol='BTCUSD',
            data_broker_type='kraken_spot', start_date=_T0,
            is_profile_run=_PROFILE_RUN.get(r.scenario_name, True))
        for r in results
    ]
    return BatchExecutionSummary(
        batch_execution_time=0.0, batch_warmup_time=0.0, batch_tickrun_time=0.0,
        process_result_list=results, single_scenario_list=scenarios)


def _build(results):
    return build_block_splitting_report_from_batch(_RUN_ID, _batch(results), [])


class TestBuild:
    def test_aggregates_blocks_per_symbol(self):
        rep = _build([
            _result('BTCUSD_vol_00', open_trades=1, open_pnl=-2.0, nat_trades=3,
                    nat_pnl=10.0, discarded=1),
            _result('BTCUSD_vol_01', open_trades=1, open_pnl=-1.0, nat_trades=1, nat_pnl=5.0),
        ])
        assert len(rep.symbols) == 1
        row = rep.symbols[0]
        assert row.symbol == 'BTCUSD' and row.generator_mode == 'unknown'
        assert row.block_count == 2
        assert row.open_at_boundary_trades == 2 and row.natural_closed_trades == 4
        assert row.total_trades == 6 and row.total_pnl == 12.0   # (-3) + 15
        assert row.discarded_pending_orders == 1
        # ratios: open-at-edge ratio 2/6; disposition |−3| / |12| * 100
        assert round(row.open_at_boundary_ratio, 2) == round(2 / 6 * 100, 2)
        assert round(row.disposition_pct, 2) == 25.0

    def test_skips_failed_and_non_profile_runs(self):
        rep = _build([
            _result('BTCUSD_vol_00', open_trades=1, nat_trades=1, success=False),  # failed → skip
            _result('BTCUSD_vol_01', open_trades=1, nat_trades=1,
                    profile_run=False),                              # not a Profile Run → skip
            _result('BTCUSD_vol_02', open_trades=2, nat_trades=2),
        ])
        assert len(rep.symbols) == 1 and rep.symbols[0].block_count == 1
        assert rep.symbols[0].total_trades == 4

    def test_multi_symbol_aggregate_sorted(self):
        rep = _build([
            _result('ETHUSD_vol_00', open_trades=1, open_pnl=-4.0, nat_trades=1, nat_pnl=4.0),
            _result('BTCUSD_vol_00', open_trades=1, open_pnl=-1.0, nat_trades=3, nat_pnl=9.0),
        ])
        assert [r.symbol for r in rep.symbols] == ['BTCUSD', 'ETHUSD']   # sorted
        assert rep.agg_total_trades == 6 and rep.agg_open_at_boundary_trades == 2
        # agg disposition = |−5| / |8| * 100
        assert round(rep.agg_disposition_pct, 2) == round(5 / 8 * 100, 2)

    def test_empty_run(self):
        rep = _build([])
        assert rep.symbols == [] and rep.agg_total_trades == 0


class TestTheDispositionStillDistinguishes:
    """
    The measure moved with #492 and the report has to keep ANSWERING, not just run.

    The block edge used to force-close every open position, so its impact arrived as
    realised P&L on `scenario_end` trades. Nothing produces that mark any more — so a
    disposition still reading it would report `0` for every block and grade every split
    GOOD, while the edge kept cutting the same trades. That is a silent constant wearing
    the face of a clean result, which is why these three cases must come out DIFFERENT.
    """

    def test_a_block_that_ends_flat_shows_no_impact(self):
        rep = _build([
            _result('BTCUSD_vol_00', open_trades=0, open_pnl=0.0,
                    nat_trades=4, nat_pnl=20.0),
        ])
        row = rep.symbols[0]

        assert row.open_at_boundary_trades == 0
        assert row.open_at_boundary_ratio == 0.0
        assert row.disposition_pct == 0.0

    def test_a_block_that_ends_holding_shows_impact(self):
        rep = _build([
            _result('BTCUSD_vol_00', open_trades=1, open_pnl=-6.0,
                    nat_trades=3, nat_pnl=18.0),
        ])
        row = rep.symbols[0]

        assert row.open_at_boundary_trades == 1
        assert row.open_at_boundary_ratio > 0.0
        # |−6| / |12| — the unrealised P&L riding on the position the edge left open
        assert round(row.disposition_pct, 2) == 50.0

    def test_two_symbols_cutting_differently_are_graded_differently(self):
        """
        The guard against the number going constant.

        Same natural result, different edge impact — if the two rows ever come out equal,
        the disposition has stopped measuring anything.
        """
        rep = _build([
            _result('BTCUSD_vol_00', open_trades=1, open_pnl=-1.0,
                    nat_trades=3, nat_pnl=11.0),
            _result('ETHUSD_vol_00', open_trades=3, open_pnl=-9.0,
                    nat_trades=3, nat_pnl=11.0),
        ])
        btc = next(r for r in rep.symbols if r.symbol == 'BTCUSD')
        eth = next(r for r in rep.symbols if r.symbol == 'ETHUSD')

        assert btc.open_at_boundary_trades != eth.open_at_boundary_trades
        assert btc.disposition_pct != eth.disposition_pct
        assert eth.disposition_pct > btc.disposition_pct, (
            'the symbol whose edge cut more must grade worse')

    def test_the_unrealised_pnl_at_the_edge_is_carried_not_dropped(self):
        """The impact is a NUMBER, not just a count — a report that lost it says nothing."""
        rep = _build([
            _result('BTCUSD_vol_00', open_trades=2, open_pnl=-7.5,
                    nat_trades=1, nat_pnl=2.5),
        ])
        row = rep.symbols[0]

        assert row.open_at_boundary_pnl == -7.5
        assert row.total_pnl == -5.0

    def test_an_unvalued_position_contributes_zero_rather_than_a_guess(self):
        """A position no tick ever priced carries 0.0 — honest, not invented."""
        rep = _build([
            _result('BTCUSD_vol_00', open_trades=1, open_pnl=0.0),
        ])
        row = rep.symbols[0]

        assert row.open_at_boundary_trades == 1
        assert row.open_at_boundary_pnl == 0.0
