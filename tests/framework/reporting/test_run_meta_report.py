"""
Run-Meta Report Builder Tests.

`build_run_meta_report_from_batch` projects the orchestrator's primary run-level measurements
(timing split + scenario identity) into `RunMetaReport` — the values PRESENT used to read straight
from `BatchExecutionSummary`. Tested with REAL BatchExecutionSummary / SingleScenario fixtures.
"""
from datetime import datetime, timezone

from python.framework.reporting.builders.run_meta_report_builder import (
    build_run_meta_report_from_batch,
    derive_run_throughput,
)
from python.framework.types.api.report_types import (
    ProfilingAggregate,
    ProfilingReport,
    RunSummary,
    ScenarioDetailsReport,
    ScenarioDetailsRow,
)
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.scenario_types.scenario_set_types import SingleScenario

# Every report artifact names its run (#475); the value is opaque to these tests.
_RUN_ID = '20260830_120000_a1b2c3d4'

_DT = datetime(2025, 1, 1, tzinfo=timezone.utc)


def _scenario(name, idx, symbol) -> SingleScenario:
    return SingleScenario(
        name=name, scenario_index=idx, symbol=symbol, data_broker_type='mt5', start_date=_DT)


def _batch(scenarios, exec_t=0.0, warmup=0.0, tickrun=0.0, pickle=0.0, pickle_mb=0.0,
           debug=False) -> BatchExecutionSummary:
    return BatchExecutionSummary(
        batch_execution_time=exec_t, batch_warmup_time=warmup, batch_tickrun_time=tickrun,
        batch_pickle_time=pickle, batch_pickle_sample_mb=pickle_mb, debug_execution=debug,
        single_scenario_list=scenarios, process_result_list=[])


def test_timing_and_identity():
    meta = build_run_meta_report_from_batch(_RUN_ID, _batch(
        [_scenario('s1', 0, 'GBPUSD'), _scenario('s2', 1, 'USDJPY'), _scenario('s3', 2, 'GBPUSD')],
        exec_t=12.5, warmup=2.0, tickrun=10.5, pickle=1.2, pickle_mb=3.4, debug=True))
    assert meta.scenario_count == 3
    assert meta.symbols == ['GBPUSD', 'USDJPY']     # sorted + deduped
    assert meta.execution_time_s == 12.5 and meta.warmup_time_s == 2.0
    assert meta.tickrun_time_s == 10.5 and meta.pickle_time_s == 1.2
    assert meta.pickle_sample_mb == 3.4 and meta.debug_execution is True
    assert meta.is_profile_run is False and meta.disabled_count == 0


def test_disabled_and_profile_run():
    """
    The disabled count comes from the LOADER, which drops a disabled scenario before any batch
    exists. This test used to plant one with `enabled = False` inside the batch — a state the
    loader never produces — so the count it asserted was one production never reached: the
    console's "(N disabled)" read 0 on every real run.
    """
    s1, s2 = _scenario('s1', 0, 'GBPUSD'), _scenario('s2', 1, 'GBPUSD')
    s1.is_profile_run = True
    meta = build_run_meta_report_from_batch(_RUN_ID, _batch([s1, s2]), disabled_count=1)
    assert meta.is_profile_run is True
    assert meta.disabled_count == 1
    assert meta.scenario_count == 2, 'a disabled scenario was never part of the batch'


def test_empty_batch():
    meta = build_run_meta_report_from_batch(_RUN_ID, _batch([]))
    assert meta.scenario_count == 0 and meta.symbols == [] and meta.is_profile_run is False
    assert not meta.worker_tracking_on and not meta.profiling_tracking_on


class TestWhatTheConsolesReadIsOnTheRunMeta:
    """
    The parallelism, the machine and the throughput the header and the executive print are on
    the run meta (#557) — read and derived once, never while a console renders.
    """

    def _meta(self, tickrun=10.0, pickle=0.0, scenarios=2):
        return build_run_meta_report_from_batch(_RUN_ID, _batch(
            [_scenario(f's{n}', n, 'GBPUSD') for n in range(scenarios)],
            tickrun=tickrun, pickle=pickle),
            parallel_scenarios=True, max_parallel_scenarios=8)

    def test_the_parallelism_and_its_concurrency(self):
        meta = self._meta(scenarios=3)
        assert meta.parallel_scenarios and meta.max_parallel_scenarios == 8
        assert meta.concurrent_scenarios == 3              # fewer scenarios than the limit
        meta.parallel_scenarios = False
        assert meta.concurrent_scenarios == 1

    def test_the_tick_run_without_its_pickling(self):
        assert self._meta(tickrun=10.5, pickle=1.5).tickrun_execution_time_s == 9.0

    def test_no_machine_reading_leaves_the_machine_unrecorded(self):
        meta = self._meta()
        assert meta.machine_cpu_count is None and meta.machine_memory_available_bytes is None

    def test_the_throughput_over_the_algo_ticks(self):
        meta = derive_run_throughput(
            self._meta(tickrun=10.0),
            ScenarioDetailsReport(run_id=_RUN_ID, units=[
                ScenarioDetailsRow(name='s0', symbol='GBPUSD', ticks_processed=600),
                ScenarioDetailsRow(name='s1', symbol='GBPUSD', ticks_processed=400)]),
            ProfilingReport(run_id=_RUN_ID, units=[]),
            RunSummary(run_id=_RUN_ID, currencies=[], unit_count=2,
                       tick_timespan_total_seconds=7200.0))
        assert (meta.total_ticks_processed, meta.loop_ticks, meta.has_clipping) == (1000, 1000, False)
        assert meta.ticks_per_second == 100.0      # 1000 ticks over 10 s
        assert meta.speedup == 720.0               # 2 h of market time over 10 s
        assert meta.ticks_per_hour == 500.0        # 1000 ticks over 2 h
        assert meta.avg_hours_per_unit == 1.0      # 2 h over 2 scenarios

    def test_a_clipping_budget_counts_what_the_loop_iterated(self):
        meta = derive_run_throughput(
            self._meta(tickrun=10.0),
            ScenarioDetailsReport(run_id=_RUN_ID, units=[
                ScenarioDetailsRow(name='s0', symbol='GBPUSD', ticks_processed=800)]),
            ProfilingReport(run_id=_RUN_ID, units=[],
                            aggregate=ProfilingAggregate(clipping_total_ticks=1000)),
            RunSummary(run_id=_RUN_ID, currencies=[], unit_count=1))
        assert (meta.loop_ticks, meta.has_clipping) == (1000, True)
        # No market time was measured: nothing to divide by
        assert (meta.speedup, meta.ticks_per_hour, meta.avg_hours_per_unit) == (0.0, 0.0, 0.0)
