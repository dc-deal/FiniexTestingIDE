"""
Run-Meta Report Builder Tests.

`build_run_meta_report_from_batch` projects the orchestrator's primary run-level measurements
(timing split + scenario identity) into `RunMetaReport` — the values PRESENT used to read straight
from `BatchExecutionSummary`. Tested with REAL BatchExecutionSummary / SingleScenario fixtures.
"""
from datetime import datetime, timezone

from python.framework.reporting.builders.run_meta_report_builder import (
    build_run_meta_report_from_batch,
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
