"""
Live session summary tests (#403 Phase 2).

`LiveSessionSummary` is the AutoTrader closing block of the unified end-of-run console: session
stats + warnings/errors (from the session buffers, §35) + output locations. Built against a real
AutoTraderResult (not a stand-in); rendered through the real ConsoleRenderer with stdout captured.
"""

from pathlib import Path

from python.framework.reporting.console.live_session_summary import LiveSessionSummary
from python.framework.types.autotrader_types.autotrader_result_types import AutoTraderResult
from python.framework.utils.console_renderer import ConsoleRenderer


def _render(result: AutoTraderResult, run_dir=None, trade_report=None) -> str:
    """Render the closing block and return the captured stdout (ANSI kept)."""
    import io
    import sys
    old = sys.stdout
    sys.stdout = buf = io.StringIO()
    try:
        LiveSessionSummary(result, trade_report, run_dir).render(ConsoleRenderer())
    finally:
        sys.stdout = old
    return buf.getvalue()


class TestLiveSessionSummary:
    """The live closing block renders the session outcome."""

    def test_renders_session_stats(self):
        result = AutoTraderResult(
            session_duration_s=4.5, ticks_processed=100, shutdown_mode='normal')
        out = _render(result, run_dir=Path('logs/autotrader/x/run'))
        assert 'AutoTrader Session Summary' in out
        assert 'Duration:' in out
        assert 'Shutdown:       normal' in out
        assert 'Log directory:  logs/autotrader/x/run' in out

    def test_emergency_cause_rendered(self):
        result = AutoTraderResult(shutdown_mode='emergency', emergency_reason='broker down')
        out = _render(result)
        assert 'EMERGENCY CAUSE: broker down' in out

    def test_warnings_not_in_closing_block(self):
        # Warnings/errors moved to the shared WarningsSummary section (#403 Phase 2 follow-up);
        # the closing block no longer lists them (only the prominent emergency cause stays).
        result = AutoTraderResult(warning_messages=['[  1s 000ms] WARNING | 1 position open'])
        out = _render(result)
        assert 'Warnings:' not in out
        assert '1 position open' not in out
