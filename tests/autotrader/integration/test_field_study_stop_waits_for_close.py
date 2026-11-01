"""
FiniexTestingIDE - The Field Study's Close at the Stop through the AutoTrader Pipeline (#630)

The field study's phase `stop_waits_for_close` sends a close and asks for the session's end in one
pass, so the close is still awaited when the loop ends: nothing drains the venue's answers between
that pass and the end. Run here as two whole mock sessions. With the stop's wait, the close is
booked before the session-end event and the phase passes on a flat book. The control runs the stop as
it was before #630, with the session-end event right after the loop: the book still holds the
position at that event, and the phase fails. A wait of no length would not do as the control: the
stop's last read books an answer that has already arrived, and this mock venue answers at once.

Spot only: the acceptance studies refuse a margin account until #209. The stop's wait itself is held
in both account models by the session-end suite (`test_stop_waits_for_orders.py`).
"""

import json
from dataclasses import replace
from pathlib import Path

import pytest

from python.configuration.autotrader.autotrader_config_loader import load_autotrader_config
from python.framework.autotrader.autotrader_main import AutotraderMain
from python.framework.autotrader.autotrader_tick_loop import AutotraderTickLoop
from python.framework.reporting.certificates.field_study_certificate import FieldStudyCertificate
from tests.shared.fixture_helpers import remove_run_dir

_PROFILE = 'configs/autotrader_profiles/mock/field_study_stop_waits_mock.json'
_PHASE = 'stop_waits_for_close'


def _session(config):
    """
    Run one mock session and read what it leaves behind.

    Args:
        config: The session's configuration

    Returns:
        The run directory, the capture's lines after its header, and the certificate's analysis
    """
    trader = AutotraderMain(config)
    trader.run()
    run_dir = Path(trader._run_dir)
    capture = run_dir / 'field_study.jsonl'
    lines = [json.loads(line) for line in capture.read_text(encoding='utf-8').splitlines()
             if line.strip()]
    return run_dir, lines[1:], FieldStudyCertificate.analyze(str(capture))


@pytest.fixture(scope='module')
def waited():
    """The session on today's stop."""
    run_dir, lines, analysis = _session(load_autotrader_config(_PROFILE))
    yield lines, analysis
    remove_run_dir(run_dir)


@pytest.fixture(scope='module')
def not_waited():
    """The same session on the stop before #630; a bot of its own, so no carry-over joins the two."""
    config = replace(load_autotrader_config(_PROFILE), bot_id='fldstop02')
    with pytest.MonkeyPatch.context() as patch:
        # Both waits go: the stop before #630 had neither
        patch.setattr(AutotraderTickLoop, '_wait_for_orders', lambda self, *args, **kwargs: None)
        run_dir, lines, analysis = _session(config)
    yield lines, analysis
    remove_run_dir(run_dir)


def _outcomes(analysis):
    return {row['phase']: (row['outcome'], row['reason']) for row in analysis['phases']}


def _close_fills(lines):
    return [line for line in lines if line['event_type'] == 'order_filled'
            and line['phase'] == _PHASE and line['extra']['action'] == 'close']


class TestAStopThatWaits:

    def test_the_phase_passes_and_the_summary_is_overtaken(self, waited):
        _, analysis = waited
        assert _outcomes(analysis) == {
            _PHASE: ('pass', 'the stop waited for the close and booked its fill'),
            'final_summary': ('skipped', f'the session ended in {_PHASE}'),
        }
        assert analysis['failed_phases'] == []
        assert analysis['missing_phases'] == []

    def test_the_close_is_booked_under_the_phase(self, waited):
        lines, _ = waited
        assert _close_fills(lines)


class TestAStopThatDoesNotWait:
    """The control: the stop before #630, whose session-end event comes before the close is booked."""

    def test_the_phase_fails_and_names_the_position_still_in_the_book(self, not_waited):
        _, analysis = not_waited
        outcome, reason = _outcomes(analysis)[_PHASE]
        assert outcome == 'fail'
        assert 'before the close was booked: 1 position(s) open in the book' in reason
        assert analysis['failed_phases'] == [_PHASE]
