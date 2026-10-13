"""
First-Tick Refusal — a session that cannot close its warmup seam says it refused (#588).

A live session reads its warmup at boot and reads it again at the first tick where a period ended in
between. When the venue has not committed that period yet, or cannot be read, the session stops
before its first decision. That is a refusal, not a crash: nothing was decided, nothing was placed,
and a restart a few seconds later closes the seam. These tests hold the console, the session log
and the result to saying exactly that — while the grading stays FAILED, so whoever relaunches
sessions relaunches it.
"""

import time
from types import SimpleNamespace

import pytest

from python.framework.autotrader import autotrader_main as autotrader_main_module
from python.framework.autotrader.autotrader_main import AutotraderMain
from python.framework.exceptions.warmup_history_errors import WarmupHistoryRefusedError
from python.framework.types.autotrader_types.autotrader_result_types import AutoTraderResult
from python.framework.types.run_outcome_types import RunOutcome
from tests.shared.recording_logger import RecordingLogger

REFUSAL = WarmupHistoryRefusedError(
    'ETHUSD M1: the venue has not yet committed the period before the first tick '
    '(2026-10-13T07:30:00.400000+00:00)')


def _session(tick_loop_started: bool) -> AutotraderMain:
    """A session reduced to what the abort report reads and sets."""
    session = AutotraderMain.__new__(AutotraderMain)
    session._tick_loop_started = tick_loop_started
    session._session_logger = RecordingLogger()
    session._global_logger = RecordingLogger()
    session._refused_at_first_tick = False
    session._emergency_reason = None
    session._emergency_error_type = None
    session._shutdown_mode = 'normal'
    return session


class TestTheAbortIsNamedForWhatItIs:

    def test_a_seam_refusal_in_the_tick_loop_is_a_refusal(self, capsys):
        session = _session(tick_loop_started=True)

        session._report_abort(REFUSAL)

        block = capsys.readouterr().out.strip().splitlines()
        assert block[1] == '  ⛔ REFUSED AT THE FIRST TICK — no decision made, no order placed'
        assert 'not yet committed' in block[2]
        assert 'RUNTIME ERROR' not in '\n'.join(block)
        assert session._refused_at_first_tick
        assert session._shutdown_mode == 'emergency'

    def test_the_refusal_reaches_the_session_log(self, capsys):
        """The session log is the operator's channel; the global log alone would hide it."""
        session = _session(tick_loop_started=True)

        session._report_abort(REFUSAL)

        assert [(level, message.startswith('⛔ Refused at the first tick'))
                for level, message in session._session_logger.lines] == [('error', True)]
        assert session._global_logger.lines == []

    def test_a_crash_in_the_tick_loop_still_reads_as_one(self, capsys):
        session = _session(tick_loop_started=True)

        session._report_abort(KeyError('position'))

        assert 'RUNTIME ERROR — SESSION ABORTED' in capsys.readouterr().out
        assert not session._refused_at_first_tick

    def test_the_same_refusal_before_the_tick_loop_is_a_startup_failure(self, capsys):
        """The boot refusals (no bars at all, a capacity too small) stay STARTUP FAILED."""
        session = _session(tick_loop_started=False)

        session._report_abort(REFUSAL)

        assert '  ❌ STARTUP FAILED' in capsys.readouterr().out
        assert not session._refused_at_first_tick


class TestTheGradingStays:

    def test_a_refused_session_is_still_failed_and_exits_2(self):
        """A relaunch is the remedy, and a supervisor relaunches on a failed exit."""
        result = AutoTraderResult(shutdown_mode='emergency', emergency_reason=str(REFUSAL),
                                  refused_at_first_tick=True)
        assert result.get_outcome() is RunOutcome.FAILED
        assert result.get_exit_code() == 2


class _Constructed(Exception):
    """Raised in place of building the result, carrying what it would have been built with."""


def test_the_result_carries_the_refusal(monkeypatch):
    """`_collect_results` hands the flag on — the summary reads it from the result."""
    session = _session(tick_loop_started=True)
    session._report_abort(REFUSAL)
    session._session_start = time.monotonic()
    session._cold_start = SimpleNamespace(situation=None, verdict=None)
    session._first_interrupt_time = 0
    session._session_end = None

    def construct(**fields):
        raise _Constructed(fields)

    monkeypatch.setattr(autotrader_main_module, 'AutoTraderResult', construct)
    with pytest.raises(_Constructed) as built:
        session._collect_results(1, 0)

    assert built.value.args[0]['refused_at_first_tick'] is True
    assert built.value.args[0]['emergency_error_type'] == 'WarmupHistoryRefusedError'
