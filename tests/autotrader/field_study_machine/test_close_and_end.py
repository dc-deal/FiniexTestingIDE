"""
Field Study — the pass that sends a close and asks for the session's end (#630).

The phase machine asks for both in one action and the study performs them in one pass, so the close
is still on its way when the loop ends and the stop has to wait for it. Held here: the order of the
two, that the event flags start afresh with the close, and that every field study profile ends with
the phase, so the certificate proves the wait at the venue.
"""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from python.framework.decision_logic.core.live_field_study.live_field_study import LiveFieldStudy
from python.framework.types.acceptance_study_types.study_phase_types import (
    PhaseAction,
    PhaseActionKind,
)
from python.framework.types.decision_event_types import SessionEndSeverity
from python.framework.types.decision_logic_types import Decision, DecisionLogicAction

_PROFILES = sorted(Path('configs/autotrader_profiles/field_study').glob('*.json')) + [
    Path('configs/autotrader_profiles/mock/field_study_mock.json')]


class _RecordingApi:
    """Records the trading API's calls the pass makes, in order."""

    def __init__(self):
        self.calls = []

    def get_open_positions(self, symbol=None):
        return [SimpleNamespace(position_id='pos_ethusd_7')]

    def is_pending_close(self, position_id):
        return False

    def close_position(self, position_id, lots=None):
        self.calls.append(('close', position_id))

    def request_session_end(self, reason, severity):
        self.calls.append(('end', reason, severity))


def _study(api, kind=PhaseActionKind.CLOSE_ALL_AND_END_SESSION):
    """A LiveFieldStudy with only what the dispatch touches, its flags left set by earlier events."""
    study = object.__new__(LiveFieldStudy)        # bypass full __init__
    study.trading_api = api
    study._symbol = 'ETHUSD'
    study._pending_action = PhaseAction(kind, 'stop_waits_for_close')
    study._filled_flag = True
    study._rejected_flag = True
    study._unaccounted_flag = True
    study._cancelled_flag = True
    study._suppressed_flag = True
    study._refusal_reason = 'an earlier refusal'
    return study


def _dispatch(study):
    """Perform the pending action the way a pass does."""
    study._execute_decision_impl(Decision(action=DecisionLogicAction.FLAT, outputs={}), None)


class TestThePassThatClosesAndEnds:

    def test_the_close_goes_out_before_the_end_request(self):
        api = _RecordingApi()

        _dispatch(_study(api))

        assert api.calls == [
            ('close', 'pos_ethusd_7'),
            ('end', 'field study complete', SessionEndSeverity.NORMAL),
        ]

    def test_the_flags_start_afresh_with_the_close(self):
        study = _study(_RecordingApi())

        _dispatch(study)

        assert not (study._filled_flag or study._rejected_flag or study._unaccounted_flag
                    or study._cancelled_flag or study._suppressed_flag)
        assert study._refusal_reason is None

    def test_an_ordinary_end_asks_in_the_same_words(self):
        api = _RecordingApi()

        _dispatch(_study(api, PhaseActionKind.END_SESSION))

        assert api.calls == [('end', 'field study complete', SessionEndSeverity.NORMAL)]


class TestEveryFieldStudyProfileProvesTheWait:

    def test_there_are_real_money_profiles_to_hold(self):
        assert any(path.parent.name == 'field_study' for path in _PROFILES)

    @pytest.mark.parametrize('path', _PROFILES, ids=lambda path: path.name)
    def test_it_closes_at_the_stop_after_the_force_close_and_before_the_summary(self, path):
        config = json.loads(path.read_text(encoding='utf-8'))
        phases = config['strategy_config']['decision_logic_config']['phase_sequence']
        assert [(phase['phase_id'], phase['phase_type']) for phase in phases[-3:]] == [
            ('force_close_all', 'force_close'),
            ('stop_waits_for_close', 'close_and_end'),
            ('final_summary', 'final_summary'),
        ]
