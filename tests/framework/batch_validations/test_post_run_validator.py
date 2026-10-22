"""
Post-Run Validator Tests (#395).

PostRunValidator lifts the batch-global advisory warnings out of the report renderer: it appends
run-scoped ValidationResults to BatchExecutionSummary.batch_validation_result. Tested with REAL
BatchExecutionSummary / SingleScenario / ClippingStats fixtures. The profiling-dependent budget
checks (P5 / too-high) need a full tick-loop result and are covered via the sim integration run.
The coordination-overhead + infra-bottleneck advisories (lifted out of the report renderers, #395)
are tested here with real tick-loop / profiling fixtures.
"""

from datetime import datetime, timedelta, timezone

from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.data_deficiency_types import DataDeficiency
from python.framework.types.performance_types.performance_stats_types import (
    DecisionLogicStats,
    WorkerCoordinatorPerformanceStats,
    WorkerPerformanceStats,
)
from python.framework.types.process_data_types import (
    ClippingStats,
    ProcessProfileData,
    ProcessResult,
    ProcessTickLoopResult,
)
from python.framework.types.run_origin_types import CodeIdentity, RepositoryState
from python.framework.types.scenario_types.scenario_set_types import SingleScenario
from python.framework.types.spread_estimate_types import ModelledSpreadSummary
from python.framework.types.trading_env_types.order_event_types import OrderEvent, OrderEventType
from python.framework.types.trading_env_types.trading_env_stats_types import ExecutionStats
from python.framework.validators.post_run_validator import PostRunValidator

# The validator names the run it validates (#475); opaque here.
_RUN_ID = '20260830_120000_a1b2c3d4'

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


def _perf_result(name, idx, worker_name='rsi', worker_avg=0.1, logic_name='',
                 logic_avg=0.0, parallel=False, saved_ms=0.0) -> ProcessResult:
    """A result carrying only what the performance verdicts read."""
    tlr = ProcessTickLoopResult(
        worker_statistics=[WorkerPerformanceStats(
            worker_type='CORE/x', worker_name=worker_name, worker_call_count=1,
            worker_total_time_ms=worker_avg, worker_avg_time_ms=worker_avg,
            worker_min_time_ms=0.0, worker_max_time_ms=worker_avg)],
        decision_statistics=DecisionLogicStats(
            decision_logic_name=logic_name, decision_avg_time_ms=logic_avg),
        coordination_statistics=WorkerCoordinatorPerformanceStats(
            parallel_workers=parallel, ticks_processed=100,
            parallel_time_saved_ms=saved_ms))
    return ProcessResult(success=True, scenario_name=name, scenario_index=idx,
                         tick_loop_results=tlr)


def _batch_results(results) -> BatchExecutionSummary:
    return BatchExecutionSummary(
        batch_execution_time=0.0, batch_warmup_time=0.0, batch_tickrun_time=0.0,
        process_result_list=results)


def _warnings(batch) -> dict:
    """Run the validator and return {check_name: joined warning text}."""
    PostRunValidator(batch, _RUN_ID).validate()
    return {finding.check: finding.message
            for vr in batch.batch_validation_result for finding in vr.findings}


def test_debug_mode():
    out = _warnings(_batch([_scenario()], debug=True))
    assert 'debug_mode' in out and 'NOT REPRESENTATIVE' in out['debug_mode']


def test_no_debug_mode_when_not_debug():
    assert 'debug_mode' not in _warnings(_batch([_scenario()], debug=False))


def test_no_advisory_for_any_declared_version():
    # The version declares a schema, not data quality — no version value is an advisory.
    # '1.10.0' also guards the lexicographic trap ('1.10.0' < '1.3.0' is True as a string).
    #
    # Read on mt5 deliberately: since the spreadless-tick advisory a Kraken version below
    # 1.6.0 IS a statement, about the collector having recorded no quote. That is a venue
    # fact, not a version one, which is exactly what this test keeps separate.
    for version in ('1.2.0', '1.3.0', '1.10.0'):
        assert _warnings(_batch([_scenario(versions=[version], broker='mt5')])) == {}


def test_unknown_version_advisory():
    # Only the ABSENCE of the field is a fact the validator can state
    out = _warnings(_batch([_scenario(versions=['unknown'], broker='kraken_spot')]))
    assert 'data_version_unknown' in out
    assert 'unknown for 1/1 file(s)' in out['data_version_unknown']
    assert 'tick_index_cli.py rebuild' in out['data_version_unknown']


def test_unknown_counted_against_all_files():
    out = _warnings(_batch([_scenario(versions=['unknown', '1.2.0', '1.3.0'])]))
    assert 'unknown for 1/3 file(s)' in out['data_version_unknown']


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
    # A clean batch is one whose data can answer every question asked of it: a known format,
    # and no deficiency measured in what it read.
    assert _warnings(_batch([_scenario(versions=['1.7.0'])])) == {}


def _read(scenario: SingleScenario, *inputs) -> SingleScenario:
    """
    Give a scenario the input files it read: (origin class, format version or None, deficiencies).

    Args:
        scenario: The scenario
        *inputs: One tuple per file — a None version marks a signal file

    Returns:
        The scenario, filled
    """
    scenario.origin_classes = [origin for origin, _, _ in inputs]
    scenario.origin_evidence_grades = ['stamped' for _ in inputs]
    scenario.data_format_versions = [version for _, version, _ in inputs if version is not None]
    scenario.data_deficiencies = [list(deficiencies) for _, _, deficiencies in inputs]
    return scenario


class TestTheDeficiencyCount:
    """
    What the data a run read LACKS is said once per run and counted (#561), measured from the
    files rather than guessed from venue and format version — which is how the spreadless
    advisory it replaces stayed silent for converted history declaring format 1.7.0.
    """

    def test_a_file_without_a_quote_is_counted(self):
        out = _warnings(_batch([_read(
            _scenario(), ('production', '1.5.0', [DataDeficiency.NO_QUOTE]))]))

        assert 'NO QUOTE — 1 of 1 input file(s)' in out['data_no_quote']
        assert 'no spread was measured there' in out['data_no_quote']
        assert '1 collected at format 1.5.0' in out['data_no_quote']

    def test_complete_data_says_nothing(self):
        # Measured, not inferred: an old MT5 format with a real spread lacks nothing, and the
        # venue no longer has to be asked first.
        assert _warnings(_batch([_read(
            _scenario(broker='mt5'), ('production', '1.0.3', []))])) == {}

    def test_external_and_collected_are_counted_apart(self):
        # The same lack has a different reason in a venue's history than in an older format.
        out = _warnings(_batch([_read(
            _scenario(),
            ('external', '1.7.0', [DataDeficiency.NO_QUOTE, DataDeficiency.NO_ARRIVAL_TIME]),
            ('production', '1.2.0', [DataDeficiency.NO_QUOTE]))]))

        assert 'NO QUOTE — 2 of 2 input file(s)' in out['data_no_quote']
        assert '1 collected at format 1.2.0 · 1 external' in out['data_no_quote']
        assert 'NO ARRIVAL TIME — 1 of 2 input file(s)' in out['data_no_arrival_time']

    def test_synthetic_is_left_to_the_signal_check_before_the_run(self):
        # Both pipelines mount signals through the pre-run check, and it says it there; a
        # post-run count would say it twice for a backtest and never for a session.
        out = _warnings(_batch([_read(
            _scenario(), ('production', None, [DataDeficiency.SYNTHETIC]))]))
        assert 'data_synthetic' not in out

    def test_external_data_has_a_finding_of_its_own_and_is_not_unstamped(self):
        # The converter stamps what it brings in, so counting it as unstamped would contradict
        # the external finding beside it.
        out = _warnings(_batch([_read(_scenario(name='history'), ('external', '1.7.0', []))]))

        assert 'EXTERNAL DATA — 1 of 1 input file(s)' in out['data_origin_external']
        assert 'never admissible for a parity measurement' in out['data_origin_external']
        assert 'history' in out['data_origin_external']
        assert 'data_origin_unstamped' not in out

    def test_a_warmup_from_external_history_is_said_although_the_ticks_are_our_own(self):
        # Needs no admission — the ticks traded on are what admission guards — and is recorded,
        # so a run whose indicators started from external bars cannot pass for one that did not.
        scenario = _read(_scenario(name='trend'), ('production', '1.7.0', []))
        scenario.external_warmup_timeframes = ['D1', 'H4']
        out = _warnings(_batch([scenario]))

        assert 'warmup history reached back before our collection' in out['data_origin_external']
        assert 'warmup from external history: trend (D1, H4)' in out['data_origin_external']
        assert 'never admissible' not in out['data_origin_external']

    def test_external_ticks_and_an_external_warmup_share_one_finding(self):
        scenario = _read(_scenario(name='history'), ('external', '1.7.0', []))
        scenario.external_warmup_timeframes = ['D1']
        out = _warnings(_batch([scenario]))

        assert 'EXTERNAL DATA — 1 of 1 input file(s)' in out['data_origin_external']
        assert 'warmup from external history: history (D1)' in out['data_origin_external']


def _modelled(ticks: int, modelled: int, factor: float = 1.0,
              median: float = 0.6) -> ModelledSpreadSummary:
    return ModelledSpreadSummary(
        factor=factor, estimator_version=1, ticks_without_quote=ticks, ticks_modelled=modelled,
        median_spread_bps=median if modelled else None, day_digest='d')


class TestTheModelledSpreadIsSaidOnce:
    """What the ticks without a quote were charged instead (#602): one line, with its counts."""

    def test_the_modelled_ticks_are_counted_with_median_factor_and_version(self):
        first, second = _scenario(name='a'), _scenario(name='b', idx=1)
        first.modelled_spread = _modelled(1_000, 990, median=0.6)
        second.modelled_spread = _modelled(500, 500, median=1.4)
        out = _warnings(_batch([first, second]))['data_modelled_spread']

        assert '1,490 of 1,500 tick(s) without a quote were given a modelled quote' in out
        assert 'median spread 0.60–1.40 bps by scenario, factor 1, estimator v1' in out
        assert '10 kept bid = ask: no usable estimate before their day' in out

    def test_factor_zero_says_no_spread_was_charged(self):
        scenario = _scenario()
        scenario.modelled_spread = _modelled(800, 0, factor=0.0)
        out = _warnings(_batch([scenario]))['data_modelled_spread']

        assert '800 tick(s) without a quote kept bid = ask (factor 0)' in out
        assert 'no spread was charged' in out

    def test_a_run_without_such_ticks_says_nothing(self):
        assert 'data_modelled_spread' not in _warnings(_batch([_scenario()]))


class TestTheBrokerSpecAgeIsSaidOnce:
    """A symbol specification read from an old cache entry (#608): one line per symbol."""

    def _fetched(self, scenario: SingleScenario, days_ago: float) -> SingleScenario:
        scenario.broker_spec_fetched_at = datetime.now(timezone.utc) - timedelta(days=days_ago)
        return scenario

    def test_an_old_entry_is_said_once_with_its_count(self):
        scenarios = [self._fetched(_scenario(name='a'), 11.2),
                     self._fetched(_scenario(name='b', idx=1), 11.2)]
        out = _warnings(_batch(scenarios))['broker_spec_stale']

        assert 'kraken_spot BTCUSD' in out and '(11 days ago)' in out
        assert '2 of 2 scenario(s)' in out
        assert 'sync --force' in out

    def test_each_old_symbol_gets_its_own_line(self):
        batch = _batch([self._fetched(_scenario(name='a'), 9),
                        self._fetched(_scenario(name='b', idx=1, symbol='ETHUSD'), 30)])
        PostRunValidator(batch, _RUN_ID).validate()
        lines = [finding.message for vr in batch.batch_validation_result
                 for finding in vr.findings if finding.check == 'broker_spec_stale']

        assert len(lines) == 2
        assert any('BTCUSD' in line for line in lines) and any('ETHUSD' in line for line in lines)

    def test_a_fresh_entry_and_a_static_broker_say_nothing(self):
        fresh = self._fetched(_scenario(name='a'), 1)
        static = _scenario(name='b', idx=1, broker='mt5', symbol='EURUSD')
        assert 'broker_spec_stale' not in _warnings(_batch([fresh, static]))


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

class TestThePerformanceVerdictsLiveHere:
    """
    The performance report used to print the parallel-penalty judgement as an inline
    RECOMMENDATION, so it sat in a renderer while its neighbours already lived in this
    validator. It is a verdict — a threshold deciding whether an advisory fires — so it
    belongs here.
    """

    def test_no_per_component_slowness_verdict_is_produced(self):
        """
        Removed deliberately, and pinned so it is not re-added by reflex: a FIXED millisecond
        threshold cannot decide 'slow'. That is only meaningful against the tick interval,
        which `_check_budget` already measures — and the two contradicted each other. See the
        note in validators/shared_advisory_checks.py.
        """
        out = _warnings(_batch_results([
            _perf_result('s1', 0, worker_name='heavy', worker_avg=25.0),
            _perf_result('s2', 1, logic_name='tunnel', logic_avg=40.0)]))
        assert 'slow_component' not in out

    def test_parallel_that_costs_time_is_flagged(self):
        out = _warnings(_batch_results([
            _perf_result('s1', 0, parallel=True, saved_ms=-12.5)]))
        assert 'parallel_penalty' in out
        assert '12.5ms' in out['parallel_penalty'] and 's1' in out['parallel_penalty']

    def test_parallel_that_saves_time_is_not(self):
        assert 'parallel_penalty' not in _warnings(_batch_results([
            _perf_result('s1', 0, parallel=True, saved_ms=30.0)]))

    def test_sequential_execution_is_never_flagged(self):
        assert 'parallel_penalty' not in _warnings(_batch_results([
            _perf_result('s1', 0, parallel=False, saved_ms=-99.0)]))


# ── Code under no version control (#551) ──────────────────────────────────────────────────────

def _identity(algos_membership):
    """A code identity whose framework is committed and whose algo directory has a given state."""
    return CodeIdentity(
        framework=RepositoryState(root='/app', commit='abc1234'),
        repositories=[RepositoryState(root='/app/user_algos/my_bot',
                                      in_repository=algos_membership)])


def _identity_warnings(identity) -> dict:
    """Run the validator over a plain batch with a recorded code identity."""
    batch = _batch([_scenario()])
    PostRunValidator(batch, _RUN_ID, code_identity=identity).validate()
    return {finding.check: finding.message
            for vr in batch.batch_validation_result for finding in vr.findings}


def test_a_strategy_under_no_version_control_is_warned_about():
    """A `user_algos/` package before `git init`: the run can never be reproduced."""
    out = _identity_warnings(_identity(algos_membership=False))
    assert 'unversioned_code' in out
    assert '/app/user_algos/my_bot · not under version control' in out['unversioned_code']
    assert 'git init' in out['unversioned_code']


def test_a_state_git_could_not_read_is_warned_about_as_unknown():
    out = _identity_warnings(_identity(algos_membership=None))
    assert 'state unknown' in out['unversioned_code']
    assert 'safe.directory' in out['unversioned_code']


def test_committed_or_merely_dirty_code_is_not_warned_about():
    """Uncommitted work in a repository is ordinary development: commit + patch restore it."""
    dirty = CodeIdentity(framework=RepositoryState(root='/app', commit='abc1234', dirty=True))
    assert 'unversioned_code' not in _identity_warnings(_identity(algos_membership=True))
    assert 'unversioned_code' not in _identity_warnings(dirty)


def test_a_run_without_a_captured_identity_is_not_warned_about():
    """A run commissioned not to report captured none — nothing is claimed, nothing is warned."""
    assert 'unversioned_code' not in _identity_warnings(None)


def _stream_result(name, idx, recorded, counted) -> ProcessResult:
    """
    A scenario whose stream holds `recorded` submissions while its executor counted `counted`.

    Args:
        name: Scenario name
        idx: Scenario index
        recorded: `submitted` events in its stream
        counted: The executor's `orders_submitted`

    Returns:
        The ProcessResult
    """
    events = [OrderEvent(seq=n + 1, event_type=OrderEventType.SUBMITTED, order_id=f'pos_{n}',
                         submitted_seq=n + 1) for n in range(recorded)]
    tlr = ProcessTickLoopResult(
        order_events=events, execution_stats=ExecutionStats(orders_submitted=counted))
    return ProcessResult(success=True, scenario_name=name, scenario_index=idx,
                         tick_loop_results=tlr)


class TestTheStreamHoldsEverySubmission:
    """
    The counters derived from a scenario's stream are only as complete as the stream (#362).

    The executor counts a submission and records its event in one statement; the count stays in
    memory, the event travels back from the subprocess. A difference is a record lost on the way.
    """

    def test_a_stream_short_of_a_submission_is_reported(self):
        out = _warnings(_batch_results([_stream_result('s1', 0, recorded=2, counted=3),
                                        _stream_result('s2', 1, recorded=4, counted=4)]))
        assert 'order_event_stream_incomplete' in out
        assert 's1 · 2 submission(s) in the stream, 3 counted' in out['order_event_stream_incomplete']
        assert 's2' not in out['order_event_stream_incomplete']

    def test_a_complete_stream_says_nothing(self):
        out = _warnings(_batch_results([_stream_result('s1', 0, recorded=3, counted=3)]))
        assert 'order_event_stream_incomplete' not in out
