"""
Post-Run Validator Tests (#395).

PostRunValidator lifts the batch-global advisory warnings out of the report renderer: it appends
run-scoped ValidationResults to BatchExecutionSummary.batch_validation_result. Tested with REAL
BatchExecutionSummary / SingleScenario / ClippingStats fixtures. The profiling-dependent budget
checks (P5 / too-high) need a full tick-loop result and are covered via the sim integration run.
The coordination-overhead + infra-bottleneck advisories (lifted out of the report renderers, #395)
are tested here with real tick-loop / profiling fixtures.
"""

from datetime import datetime, timezone

from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.performance_types.performance_stats_types import (
    DecisionLogicStats, WorkerPerformanceStats)
from python.framework.types.process_data_types import (
    ClippingStats, ProcessProfileData, ProcessResult, ProcessTickLoopResult)
from python.framework.types.scenario_types.scenario_set_types import SingleScenario
from python.framework.validators.post_run_validator import PostRunValidator

_DT = datetime(2025, 10, 13, tzinfo=timezone.utc)


def _scenario(name='s1', idx=0, symbol='BTCUSD', broker='kraken_spot',
              versions=None, stress=None) -> SingleScenario:
    s = SingleScenario(
        name=name, scenario_index=idx, symbol=symbol, data_broker_type=broker, start_date=_DT)
    if versions is not None:
        s.data_format_versions = versions
    s.stress_test_config = stress
    return s


def _batch(scenarios, debug=False, clipping=None) -> BatchExecutionSummary:
    return BatchExecutionSummary(
        batch_execution_time=0.0, batch_warmup_time=0.0, batch_tickrun_time=0.0,
        single_scenario_list=scenarios, debug_execution=debug, clipping_stats_map=clipping or {})


def _ws(total_ms) -> WorkerPerformanceStats:
    return WorkerPerformanceStats(
        worker_type='CORE/x', worker_name='x', worker_call_count=1, worker_total_time_ms=total_ms,
        worker_avg_time_ms=total_ms, worker_min_time_ms=0.0, worker_max_time_ms=total_ms)


def _result_prof(name, idx, profile_times, worker_ms=0.0, decision_ms=0.0) -> ProcessResult:
    tlr = ProcessTickLoopResult(
        worker_statistics=[_ws(worker_ms)] if worker_ms else [],
        decision_statistics=DecisionLogicStats(decision_total_time_ms=decision_ms),
        profiling_data=ProcessProfileData(profile_times=profile_times, profile_counts={}))
    return ProcessResult(success=True, scenario_name=name, scenario_index=idx, tick_loop_results=tlr)


def _batch_results(results) -> BatchExecutionSummary:
    return BatchExecutionSummary(
        batch_execution_time=0.0, batch_warmup_time=0.0, batch_tickrun_time=0.0,
        process_result_list=results)


def _warnings(batch) -> dict:
    """Run the validator and return {check_name: joined warning text}."""
    PostRunValidator(batch).validate()
    return {vr.scenario_name: '\n'.join(vr.warnings) for vr in batch.batch_validation_result}


def test_debug_mode():
    out = _warnings(_batch([_scenario()], debug=True))
    assert 'debug_mode' in out and 'NOT REPRESENTATIVE' in out['debug_mode']


def test_no_debug_mode_when_not_debug():
    assert 'debug_mode' not in _warnings(_batch([_scenario()], debug=False))


def test_data_version_with_kraken_caveat():
    out = _warnings(_batch([_scenario(versions=['1.2.0'], broker='kraken_spot')]))
    assert 'data_version' in out
    assert 'pre-V1.3.0 files (1/1)' in out['data_version']
    assert 'Kraken' in out['data_version']


def test_no_data_version_when_current():
    assert 'data_version' not in _warnings(_batch([_scenario(versions=['1.3.0'])]))


def test_stress_test():
    stress = {'reject_open_order': {'enabled': True, 'probability': 0.1, 'seed': 42}}
    out = _warnings(_batch([_scenario(stress=stress)]))
    assert 'stress_test' in out
    assert 'STRESS TEST ACTIVE' in out['stress_test'] and 'probability=10%' in out['stress_test']


def test_budget_granularity():
    clipping = {0: ClippingStats(ticks_total=100, ticks_kept=100, ticks_clipped=0, budget_ms=0.3)}
    out = _warnings(_batch([_scenario()], clipping=clipping))
    assert 'budget_granularity' in out
    assert '0.3ms' in out['budget_granularity'] and 'below data granularity' in out['budget_granularity']


def test_clean_batch_no_warnings():
    assert _warnings(_batch([_scenario(versions=['1.3.0'])])) == {}


def test_coordination_overhead():
    # op_total 100, computation 30 (worker 20 + decision 10) → overhead 70 / 30 > 0.5
    r = _result_prof('s1', 0, {'worker_decision': 100.0, 'total_per_tick': 100.0},
                     worker_ms=20.0, decision_ms=10.0)
    out = _warnings(_batch_results([r]))
    assert 'coordination_overhead' in out and 'exceeds 50%' in out['coordination_overhead']


def test_no_overhead_when_low():
    # computation 90 (worker 60 + decision 30) → overhead 10 / 90 < 0.5
    r = _result_prof('s1', 0, {'worker_decision': 100.0, 'total_per_tick': 100.0},
                     worker_ms=60.0, decision_ms=30.0)
    assert 'coordination_overhead' not in _warnings(_batch_results([r]))


def test_infra_bottleneck():
    # live_update is the dominant op (infra) → advisory
    r = _result_prof('s1', 0, {'live_update': 80.0, 'worker_decision': 20.0, 'total_per_tick': 100.0})
    out = _warnings(_batch_results([r]))
    assert 'bottleneck' in out and "'live_update'" in out['bottleneck']


def test_expected_bottleneck_no_warning():
    # worker_decision (hot path) is the dominant op → no advisory
    r = _result_prof('s1', 0, {'worker_decision': 80.0, 'live_update': 20.0, 'total_per_tick': 100.0})
    assert 'bottleneck' not in _warnings(_batch_results([r]))
