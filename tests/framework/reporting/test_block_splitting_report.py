"""
Block-Splitting Report Builder Tests.

`build_block_splitting_report_from_batch` aggregates the per-block `BlockBoundaryReport`s of a
Profile Run into per-symbol disposition facts + ratios. Tested with REAL BatchExecutionSummary /
ProcessResult / BlockBoundaryReport fixtures (the generator-mode lookup is a trivial dict, tested
via the 'unknown' fallback).
"""
from python.framework.reporting.builders.block_splitting_report_builder import (
    build_block_splitting_report_from_batch)
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.process_data_types import (
    BlockBoundaryReport, ProcessResult, ProcessTickLoopResult)


def _bbr(fc_trades=0, fc_pnl=0.0, nat_trades=0, nat_pnl=0.0, discarded=0) -> BlockBoundaryReport:
    return BlockBoundaryReport(
        force_closed_trades=fc_trades, force_closed_pnl=fc_pnl,
        natural_closed_trades=nat_trades, natural_closed_pnl=nat_pnl,
        discarded_pending_orders=discarded)


def _result(name, bbr, success=True, idx=0) -> ProcessResult:
    return ProcessResult(
        success=success, scenario_name=name, scenario_index=idx,
        tick_loop_results=ProcessTickLoopResult(block_boundary_report=bbr))


def _batch(results) -> BatchExecutionSummary:
    return BatchExecutionSummary(
        batch_execution_time=0.0, batch_warmup_time=0.0, batch_tickrun_time=0.0,
        process_result_list=results, single_scenario_list=[])


def _build(results):
    return build_block_splitting_report_from_batch(_batch(results), [])


class TestBuild:
    def test_aggregates_blocks_per_symbol(self):
        rep = _build([
            _result('BTCUSD_vol_00', _bbr(fc_trades=1, fc_pnl=-2.0, nat_trades=3, nat_pnl=10.0, discarded=1)),
            _result('BTCUSD_vol_01', _bbr(fc_trades=1, fc_pnl=-1.0, nat_trades=1, nat_pnl=5.0)),
        ])
        assert len(rep.symbols) == 1
        row = rep.symbols[0]
        assert row.symbol == 'BTCUSD' and row.generator_mode == 'unknown'
        assert row.block_count == 2
        assert row.force_closed_trades == 2 and row.natural_closed_trades == 4
        assert row.total_trades == 6 and row.total_pnl == 12.0   # (-3) + 15
        assert row.discarded_pending_orders == 1
        # ratios: force-close ratio 2/6; disposition |−3| / |12| * 100
        assert round(row.force_close_ratio, 2) == round(2 / 6 * 100, 2)
        assert round(row.disposition_pct, 2) == 25.0

    def test_skips_failed_and_missing_reports(self):
        rep = _build([
            _result('BTCUSD_vol_00', _bbr(fc_trades=1, nat_trades=1), success=False),  # failed → skip
            _result('BTCUSD_vol_01', None),                                            # no report → skip
            _result('BTCUSD_vol_02', _bbr(fc_trades=2, nat_trades=2)),
        ])
        assert len(rep.symbols) == 1 and rep.symbols[0].block_count == 1
        assert rep.symbols[0].total_trades == 4

    def test_multi_symbol_aggregate_sorted(self):
        rep = _build([
            _result('ETHUSD_vol_00', _bbr(fc_trades=1, fc_pnl=-4.0, nat_trades=1, nat_pnl=4.0)),
            _result('BTCUSD_vol_00', _bbr(fc_trades=1, fc_pnl=-1.0, nat_trades=3, nat_pnl=9.0)),
        ])
        assert [r.symbol for r in rep.symbols] == ['BTCUSD', 'ETHUSD']   # sorted
        assert rep.agg_total_trades == 6 and rep.agg_force_closed_trades == 2
        # agg disposition = |−5| / |8| * 100
        assert round(rep.agg_disposition_pct, 2) == round(5 / 8 * 100, 2)

    def test_empty_run(self):
        rep = _build([])
        assert rep.symbols == [] and rep.agg_total_trades == 0
