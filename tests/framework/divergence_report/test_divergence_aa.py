"""
The A/A of a deployment's code (#512): one window backtested on the release the sessions ran and on
the current tree, with the same inputs, and the two held against each other event by event — the
set both stands can read, the comparison, the verdict and the refusals before anything runs.

The two backtests themselves run as processes of their own trees and cannot run here; what they
would compare is built from the stream's own record shapes.
"""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from python.configuration.app_config_manager import AppConfigManager
from python.framework.factory.recorded_scenario_builder import (
    RENDERED_EXECUTION_TARGETS,
    SHARED_EXECUTION_FIELDS,
    RecordedScenarioBuilder,
)
from python.framework.factory.strategy_config_renderer import LOGIC_TYPE_KEY, WORKER_TYPE_KEY
from python.framework.reporting.builders.divergence.aa_comparison_builder import (
    WHOLE_RECORD,
    compare_order_events,
    compare_trades,
    fills_of,
)
from python.framework.reporting.divergence_aa_coordinator import aa_refusal
from python.framework.types.api.divergence_report_types import (
    AaDifference,
    AaReport,
    AaStand,
    AaStandRun,
    AaVerdict,
    AaWindowResult,
    RenderStamp,
)
from python.framework.types.run_purpose_types import RunPurpose
from python.framework.types.scenario_types.recorded_scenario_types import RecordedScenarioInputs
from python.framework.validators.divergence_aa_validator import (
    aa_exit_code,
    judge_aa_window,
    same_commit,
)

_UTC = timezone.utc
_SESSION_COMMIT = '33989482'
_RENDERED_AT = '2026-11-08T03:00:00+00:00'


def _profile() -> dict:
    """A rendered AutoTrader profile — only what the builder reads."""
    depth = AppConfigManager().get_bar_max_history()
    return {
        'kind': 'autotrader_rendered',
        'blocks': {
            'symbol': 'ETHUSD', 'broker_type': 'kraken_spot', 'scenario_settings': None,
            'execution': {'parallel_workers': False, 'bar_max_history': depth,
                          'heartbeat_interval_ms': 500},
            'order_guard': {'cooldown_seconds': 45.0},
            'state_frames': {'enabled': True, 'cadence_timeframe': 'M1'},
        },
        'strategy_config': {
            'decision_logic_type': 'CORE/example_logic',
            'decision_logic_config': {'threshold': 0.4, LOGIC_TYPE_KEY: 'CORE/example_logic'},
            'worker_instances': {'trend': 'CORE/example_worker'},
            'workers': {'trend': {'period': 20, WORKER_TYPE_KEY: 'CORE/example_worker'}},
        },
        'market_entry': {},
    }


def _shared(record_frames: bool) -> dict:
    inputs = RecordedScenarioInputs(
        scenario_set_name='aa_example_window_1_release', scenario_name='window_1',
        rendered_profile=_profile(), warmup_from_run='20261102_210527_ab12cd34',
        broker_config_document='3f1d' * 16, start=datetime(2026, 8, 15, tzinfo=_UTC),
        end=datetime(2026, 11, 6, tzinfo=_UTC), balances={'USD': 800.0, 'ETH': 0.0},
        run_purpose=RunPurpose.FIXTURE)
    return RecordedScenarioBuilder.build_shared(inputs, record_frames=record_frames)


class TestTheSetBothStandsRead:

    def test_the_release_set_holds_only_what_both_stands_read(self):
        document = _shared(record_frames=False)
        assert document['global']['execution_config'] == {'parallel_workers': False,
                                                          'heartbeat_interval_ms': 500}
        assert document['global']['trade_simulator_config'] == {
            'balances': {'USD': 800.0, 'ETH': 0.0}}
        scenario = document['scenarios'][0]
        assert 'warmup_from_run' not in scenario and 'broker_config_document' not in scenario
        assert document['global']['strategy_config']['workers'] == {'trend': {'period': 20}}

    def test_only_the_current_set_records_frames(self):
        assert _shared(record_frames=True)['global']['execution_config']['state_frames'] == {
            'enabled': True, 'cadence_timeframe': 'M1'}
        document = _shared(record_frames=True)
        document['global']['execution_config'].pop('state_frames')
        assert document == _shared(record_frames=False)

    def test_every_shared_execution_field_is_one_the_comparison_carries(self):
        assert set(SHARED_EXECUTION_FIELDS) <= set(RENDERED_EXECUTION_TARGETS)


def _event(seq: int, event_type: str = 'submitted', **values) -> dict:
    return {'scenario_name': 'window_1', 'seq': seq, 'event_type': event_type,
            'order_id': 'pos_ethusd_1', **values}


class TestTheComparison:

    def test_two_equal_streams_differ_nowhere(self):
        events = [_event(1, lots=0.01), _event(2, 'filled', fill_price=3000.0)]
        assert compare_order_events(events, [dict(e) for e in events]) == []

    def test_a_differing_value_is_named_with_its_position_and_both_values(self):
        difference, = compare_order_events([_event(1), _event(2, 'filled', fill_price=3000.0)],
                                           [_event(1), _event(2, 'filled', fill_price=3000.5)])
        assert (difference.stream, difference.position, difference.field) == (
            'order_events', 2, 'fill_price')
        assert (difference.release_value, difference.current_value) == ('3000.0', '3000.5')

    def test_a_value_left_out_is_null_and_differs_from_a_value(self):
        difference, = compare_order_events([_event(1)], [_event(1, fee=0.02)])
        assert (difference.field, difference.release_value, difference.current_value) == (
            'fee', 'null', '0.02')

    def test_a_record_only_one_side_holds_is_named_whole(self):
        difference, = compare_order_events([_event(1)], [_event(1), _event(2)])
        assert (difference.position, difference.field, difference.release_value) == (
            2, WHOLE_RECORD, None)

    def test_a_trade_field_only_one_stand_writes_is_named_not_compared(self):
        differences, one_sided = compare_trades(
            [{'position_id': 'p1', 'net_pnl': 1.5}],
            [{'position_id': 'p1', 'net_pnl': 1.5, 'new_figure': 7}])
        assert differences == [] and one_sided == ['new_figure']

    def test_fills_are_counted_partial_or_whole(self):
        assert fills_of([_event(1), _event(2, 'partially_filled'), _event(3, 'filled')]) == 2


def _run(stand: AaStand, **values) -> AaStandRun:
    defaults = {'run_id': f'run_{stand.value}', 'framework_commit': _SESSION_COMMIT
                if stand is AaStand.RELEASE else '5eb80acd', 'framework_dirty': False,
                'broker_config_id': 'broker_1', 'order_events': 63, 'fills': 15, 'trades': 15}
    return AaStandRun(stand=stand, tree=f'/trees/{stand.value}',
                      scenario_set_name=f'aa_x_window_1_{stand.value}', **{**defaults, **values})


def _window(release=None, current=None, before=None, after=None, differences=0) -> AaWindowResult:
    stamps = [RenderStamp(timeframe='D1', bar_file='ETHUSD_D1.parquet', rendered_at=_RENDERED_AT)]
    return AaWindowResult(
        index=1, start=datetime(2026, 8, 15, tzinfo=_UTC), end=datetime(2026, 11, 6, tzinfo=_UTC),
        release=release or _run(AaStand.RELEASE), current=current or _run(AaStand.CURRENT),
        render_stamps_before=stamps if before is None else before,
        render_stamps_after=stamps if after is None else after,
        difference_count=differences,
        differences=[AaDifference(stream='order_events', position=1, field='lots')] * differences)


class TestTheVerdict:

    def test_the_same_events_on_the_claimed_code_are_identical(self):
        assert judge_aa_window(_window(), _SESSION_COMMIT).verdict is AaVerdict.IDENTICAL

    def test_a_difference_on_the_claimed_code_is_one(self):
        assert judge_aa_window(_window(differences=2), _SESSION_COMMIT).verdict is (
            AaVerdict.DIFFERENT)

    @pytest.mark.parametrize('window, cause', [
        (_window(release=_run(AaStand.RELEASE, framework_commit='0123abcd')), 'not the sessions'),
        (_window(release=_run(AaStand.RELEASE, framework_dirty=True)), 'uncommitted'),
        (_window(current=_run(AaStand.CURRENT, framework_dirty=True)), 'no single commit'),
        (_window(current=_run(AaStand.CURRENT, broker_config_id='broker_2')),
         'different broker documents'),
        (_window(after=[RenderStamp(timeframe='D1', bar_file='ETHUSD_D1.parquet',
                                    rendered_at='2026-11-09T03:00:00+00:00')]),
         'rendered between the two runs'),
        (_window(before=[], after=[]), 'no bar file'),
        (_window(current=_run(AaStand.CURRENT, trades=None)), 'could not be read'),
        (_window(release=_run(AaStand.RELEASE, failed_because='its backtest ended with exit '
                                                              'code 2')), 'release: its backtest'),
    ])
    def test_inputs_nobody_can_vouch_for_are_not_checked_never_a_pass(self, window, cause):
        judged = judge_aa_window(window, _SESSION_COMMIT)
        assert judged.verdict is AaVerdict.NOT_CHECKED and cause in judged.not_checked_because

    def test_not_checked_holds_even_where_values_differ(self):
        window = _window(current=_run(AaStand.CURRENT, framework_dirty=True), differences=3)
        assert judge_aa_window(window, _SESSION_COMMIT).verdict is AaVerdict.NOT_CHECKED

    def test_a_short_and_a_long_hash_name_one_commit(self):
        assert same_commit('33989482', '33989482a1b2c3d4')
        assert not same_commit('339894', '339894aa') and not same_commit('', '33989482')


def _report(*verdicts: AaVerdict, refused: str = '') -> AaReport:
    return AaReport(report_id='aa_x', created_at=datetime(2026, 11, 20, tzinfo=_UTC),
                    deployment_id='my_deployment', refused_because=refused,
                    windows=[_window().model_copy(update={'verdict': v}) for v in verdicts])


class TestTheExitCode:

    def test_every_window_identical_is_zero(self):
        assert aa_exit_code(_report(AaVerdict.IDENTICAL, AaVerdict.IDENTICAL)) == 0

    def test_a_window_that_differs_is_three(self):
        assert aa_exit_code(_report(AaVerdict.IDENTICAL, AaVerdict.DIFFERENT,
                                    AaVerdict.NOT_CHECKED)) == 3

    def test_refused_no_window_or_one_not_checked_is_two(self):
        assert aa_exit_code(_report(refused='no data home')) == 2
        assert aa_exit_code(_report()) == 2
        assert aa_exit_code(_report(AaVerdict.IDENTICAL, AaVerdict.NOT_CHECKED)) == 2


class TestTheRefusals:

    @pytest.fixture
    def release_tree(self, tmp_path) -> Path:
        tree = tmp_path / 'release'
        (tree / 'python' / 'cli').mkdir(parents=True)
        (tree / 'python' / 'cli' / 'strategy_runner_cli.py').write_text('', encoding='utf-8')
        return tree

    def test_without_a_data_home_nothing_runs(self, release_tree):
        assert 'FINIEX_DATA_HOME' in aa_refusal(None, release_tree, [_SESSION_COMMIT], True)

    def test_a_data_home_holding_code_is_refused(self, tmp_path, release_tree):
        (tmp_path / 'home' / 'python').mkdir(parents=True)
        assert 'python/ directory' in aa_refusal(tmp_path / 'home', release_tree,
                                                 [_SESSION_COMMIT], True)

    def test_a_tree_without_the_strategy_runner_is_refused(self, tmp_path):
        assert 'holds no' in aa_refusal(tmp_path, tmp_path / 'nothing', [_SESSION_COMMIT], True)

    def test_sessions_on_two_commits_are_refused(self, tmp_path, release_tree):
        assert '2 framework commit' in aa_refusal(tmp_path, release_tree,
                                                  [_SESSION_COMMIT, '5eb80acd'], True)

    def test_a_good_setup_runs(self, tmp_path, release_tree):
        assert aa_refusal(tmp_path, release_tree, [_SESSION_COMMIT], True) == ''
