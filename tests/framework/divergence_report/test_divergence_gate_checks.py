"""
The divergence gate's checks beyond the code path (#512): strategy, strategy configuration,
broker document, window, and what the backtest recorded.

Every test starts from a pair the gate lets through and changes one thing; the code path is
judged over an empty tree here, so it refuses nothing and the check under test stands alone. Every
refusal names its cause and carries a check id the catalog declares.
"""

import copy
import dataclasses
from datetime import timedelta

import pytest

from python.framework.types.validation_types import Severity
from python.framework.validators.divergence_gate_validator import (
    BROKER_CONFIG_CHECK,
    FEATURE_BEYOND_VENUE_CHECK,
    FRAMES_MISSING_CHECK,
    STRATEGY_CHECK,
    STRATEGY_CONFIG_CHECK,
    WINDOW_CHECK,
    check_divergence_gate,
)
from python.framework.validators.validation_check_catalog import VALIDATION_CHECKS_BY_ID
from tests.shared.divergence_gate_pair import (
    BACKTEST_COMMIT,
    RENDERED_STRATEGY,
    SCENARIO,
    STRATEGY_ROOT_HERE,
    WINDOW_END,
    backtest_row,
    backtest_scenario,
    code_identity,
    empty_tree,
    gate_backtest,
    gate_session,
    gate_window,
)
from tests.shared.divergence_live_chain import FIRST_TICK


def _gate(sessions=None, backtest=None, window=None):
    return check_divergence_gate(sessions or [gate_session()], backtest or gate_backtest(),
                                 window or gate_window(), read_tree=empty_tree)


def _refusals(result, check):
    return [finding for finding in result.findings if finding.check == check]


def _backtest_identity(**changes):
    return code_identity(framework_commit=BACKTEST_COMMIT, strategy_root=STRATEGY_ROOT_HERE,
                         **changes)


def test_the_pair_as_built_is_comparable():
    result = _gate()
    assert result.is_comparable(), result.findings
    assert result.code_path is not None and result.code_path.files == []


def test_no_session_is_a_usage_fault():
    with pytest.raises(ValueError, match='none was given'):
        check_divergence_gate([], gate_backtest(), gate_window(), read_tree=empty_tree)


class TestTheStrategy:

    def test_a_component_of_different_code_is_refused(self):
        result = _gate(backtest=gate_backtest(identity=_backtest_identity(
            decision_digest='0' * 64)))
        refusal, = _refusals(result, STRATEGY_CHECK)
        assert 'my_algo/decision.py is different code' in refusal.message

    def test_a_component_on_one_side_only_is_refused(self):
        identity = _backtest_identity()
        identity.components = identity.components[:1]
        refusal, = _refusals(_gate(backtest=gate_backtest(identity=identity)), STRATEGY_CHECK)
        assert 'my_worker runs only in the session' in refusal.message

    def test_the_strategy_repository_on_another_commit_is_refused(self):
        result = _gate(backtest=gate_backtest(identity=_backtest_identity(
            strategy_commit='999999999999')))
        refusal, = _refusals(result, STRATEGY_CHECK)
        assert 'strategy repository is at' in refusal.message

    def test_the_strategy_repository_uncommitted_on_either_side_is_refused(self):
        dirty = _backtest_identity()
        dirty.repositories[0].dirty = True
        assert _refusals(_gate(backtest=gate_backtest(identity=dirty)), STRATEGY_CHECK)
        live_dirty = code_identity()
        live_dirty.repositories[0].dirty = True
        assert _refusals(_gate(sessions=[gate_session(identity=live_dirty)]), STRATEGY_CHECK)

    def test_a_repository_compares_by_its_label_never_by_its_root(self):
        assert STRATEGY_ROOT_HERE != code_identity().repositories[0].root
        assert not _refusals(_gate(), STRATEGY_CHECK)


class TestTheRenderedStrategy:

    @staticmethod
    def _with(change):
        strategy = copy.deepcopy(RENDERED_STRATEGY)
        change(strategy)
        return _gate(backtest=gate_backtest(scenarios=[backtest_scenario(strategy=strategy)]))

    def test_a_deep_value_is_refused_by_its_path(self):
        result = self._with(lambda s: s['decision_logic_config']['levels'].__setitem__(1, 5))
        refusal, = _refusals(result, STRATEGY_CONFIG_CHECK)
        assert 'decision_logic_config.levels[1]' in refusal.message
        assert refusal.scope == SCENARIO

    def test_a_default_filled_in_on_one_side_only_is_refused(self):
        result = self._with(lambda s: s['workers']['my_worker'].__setitem__('smoothing', 2))
        assert 'workers.my_worker.smoothing' in _refusals(
            result, STRATEGY_CONFIG_CHECK)[0].message

    def test_key_order_does_not_count(self):
        reordered = dict(reversed(list(RENDERED_STRATEGY.items())))
        assert not _refusals(_gate(backtest=gate_backtest(
            scenarios=[backtest_scenario(strategy=reordered)])), STRATEGY_CONFIG_CHECK)

    def test_a_session_whose_rendered_strategy_cannot_be_read_is_refused(self):
        session = dataclasses.replace(gate_session(), rendered_strategy_config=None,
                                      unreadable={'rendered_config': 'no such document'})
        refusal, = _refusals(_gate(sessions=[session]), STRATEGY_CONFIG_CHECK)
        assert 'no such document' in refusal.message

    def test_a_scenario_whose_rendered_strategy_cannot_be_read_is_refused(self):
        scenario = dataclasses.replace(backtest_scenario(), rendered_strategy_config=None)
        assert _refusals(_gate(backtest=gate_backtest(scenarios=[scenario])),
                         STRATEGY_CONFIG_CHECK)


class TestTheBrokerDocument:

    def test_another_frozen_configuration_is_refused(self):
        result = _gate(backtest=gate_backtest(rows=[backtest_row(broker_config_id='broker_2')]))
        refusal, = _refusals(result, BROKER_CONFIG_CHECK)
        assert 'broker_1' in refusal.message and 'broker_2' in refusal.message

    def test_a_session_without_one_is_refused(self):
        session = dataclasses.replace(gate_session(), broker_config_id='')
        assert _refusals(_gate(sessions=[session]), BROKER_CONFIG_CHECK)


class TestTheWindow:

    def test_a_backtest_that_began_late_is_refused_with_both_instants(self):
        late = FIRST_TICK + timedelta(seconds=121)
        result = _gate(backtest=gate_backtest(scenarios=[backtest_scenario(first_tick=late)]))
        refusal, = _refusals(result, WINDOW_CHECK)
        assert late.isoformat() in refusal.message and FIRST_TICK.isoformat() in refusal.message

    def test_a_first_tick_within_the_gap_bound_passes(self):
        result = _gate(backtest=gate_backtest(scenarios=[backtest_scenario(
            first_tick=FIRST_TICK + timedelta(seconds=120))]))
        assert not _refusals(result, WINDOW_CHECK)

    def test_a_backtest_that_ended_early_is_refused(self):
        early = WINDOW_END - timedelta(seconds=121)
        result = _gate(backtest=gate_backtest(scenarios=[backtest_scenario(last_tick=early)]))
        assert 'before the window' in _refusals(result, WINDOW_CHECK)[0].message

    def test_a_tick_limit_is_refused(self):
        result = _gate(backtest=gate_backtest(scenarios=[backtest_scenario(
            ends_on_tick_limit=True)]))
        assert 'tick limit' in _refusals(result, WINDOW_CHECK)[0].message

    def test_a_scenario_that_processed_no_tick_is_refused(self):
        result = _gate(backtest=gate_backtest(scenarios=[backtest_scenario(
            first_tick=None, last_tick=None)]))
        assert _refusals(result, WINDOW_CHECK)

    def test_a_window_without_an_end_refuses_the_pair(self):
        assert _refusals(_gate(window=gate_window(end=None)), WINDOW_CHECK)

    def test_a_backtest_without_a_scenario_is_refused(self):
        assert _refusals(_gate(backtest=gate_backtest(scenarios=[])), WINDOW_CHECK)


class TestWhatTheBacktestRecorded:

    def test_an_order_feature_beyond_the_venue_is_refused(self):
        result = _gate(backtest=gate_backtest(rows=[backtest_row(
            features_beyond_venue='stop_limit')]))
        assert 'stop_limit' in _refusals(result, FEATURE_BEYOND_VENUE_CHECK)[0].message

    def test_a_backtest_without_a_ledger_row_is_refused(self):
        assert _refusals(_gate(backtest=gate_backtest(rows=[])), FEATURE_BEYOND_VENUE_CHECK)

    def test_a_scenario_without_state_frames_is_refused(self):
        result = _gate(backtest=gate_backtest(scenarios=[backtest_scenario(
            records_frames=False)]))
        assert _refusals(result, FRAMES_MISSING_CHECK)


def test_every_refusal_is_an_error_with_a_declared_check():
    changed = copy.deepcopy(RENDERED_STRATEGY)
    changed['decision_logic_config']['threshold'] = 0.9
    result = _gate(backtest=gate_backtest(
        identity=_backtest_identity(decision_digest='0' * 64),
        rows=[backtest_row(broker_config_id='broker_2', features_beyond_venue='stop_limit')],
        scenarios=[backtest_scenario(ends_on_tick_limit=True, records_frames=False,
                                     strategy=changed)]))
    assert len({finding.check for finding in result.findings}) >= 6
    assert not result.is_comparable()
    for finding in result.findings:
        assert finding.severity is Severity.ERROR
        assert finding.check in VALIDATION_CHECKS_BY_ID, finding.check
        assert finding.message.endswith('.'), finding.message
