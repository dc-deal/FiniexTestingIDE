"""
The `signal_delay_minutes` resolution lever (#141 Part 2a).

A sweep knob, not a model of the archive. It answers one open question: **is the strategy's
edge latency?** Sweeping an added delay (0 / 1 / 5 / 15 min) against P&L decides whether the
heartbeat-delivered transport is sufficient or whether the event loop (#375 / #461) has to
move ahead of live hardening. A flat curve settles it one way, a collapse in the first
minutes the other.

The sweep's zero column really is zero: the archive carries no unrecorded delay — measured
against the producer's journal, envelope for envelope.

It doubles as a #390 robustness knob, which is why it is a worker parameter rather than a
test-only switch.
"""

from types import SimpleNamespace

from conftest import SYMBOL, make_provider, snapshot, utc

from python.framework.workers.abstract_signal_worker import AbstractSignalWorker
from python.framework.workers.core.llm_sentiment_worker import LlmSentimentWorker


def build(mock_logger, delay_minutes, max_staleness=600, *snapshots) -> LlmSentimentWorker:
    """A sentiment worker with a configured resolution delay."""
    worker = LlmSentimentWorker(
        name='sentiment',
        parameters={'max_staleness_minutes': max_staleness,
                    'signal_delay_minutes': delay_minutes},
        logger=mock_logger,
        trading_context=SimpleNamespace(symbol=SYMBOL))
    worker.set_signal_provider(make_provider(*snapshots))
    return worker


def at(hour, minute, score) -> object:
    """A snapshot at a time, identifiable by its score."""
    return snapshot(utc(2026, 1, 15, hour, minute), score=score, confidence=0.9,
                    signal='BUY')


class TestNoDelay:
    """The default must change nothing at all."""

    def test_zero_delay_resolves_at_the_moment_itself(self, mock_logger):
        worker = build(mock_logger, 0, 600, at(8, 0, 0.1), at(8, 10, 0.2))
        assert worker.compute_signal_at(
            utc(2026, 1, 15, 8, 11)).outputs['sentiment_score'] == 0.2

    def test_the_parameter_defaults_to_zero(self):
        """
        Every existing scenario and profile keeps resolving exactly as before, which is
        what lets the lever ship without touching a single stored result.
        """
        assert AbstractSignalWorker.get_parameter_schema()[
            'signal_delay_minutes'].default == 0


class TestDelayedResolution:
    """What the lever actually moves."""

    def test_the_delay_serves_the_earlier_snapshot(self, mock_logger):
        """Resolving as-of 08:06 reaches the 08:00 envelope, not the 08:10 one."""
        worker = build(mock_logger, 5, 600, at(8, 0, 0.1), at(8, 10, 0.2))
        assert worker.compute_signal_at(
            utc(2026, 1, 15, 8, 11)).outputs['sentiment_score'] == 0.1

    def test_a_delay_past_the_whole_history_goes_blind(self, mock_logger):
        """A gap, not an error: the lever can legitimately be swept past the archive."""
        worker = build(mock_logger, 120, 600, at(8, 0, 0.1))
        result = worker.compute_signal_at(utc(2026, 1, 15, 8, 30))
        assert result.outputs['confidence'] == 0.0
        assert result.outputs['reasoning'] == 'No signal data'

    def test_the_delay_only_shifts_it_does_not_skip(self, mock_logger):
        """The same series is walked, one window later."""
        worker = build(mock_logger, 10, 600,
                       at(8, 0, 0.1), at(8, 10, 0.2), at(8, 20, 0.3))
        scores = [worker.compute_signal_at(
            utc(2026, 1, 15, 8, m)).outputs['sentiment_score'] for m in (11, 21, 31)]
        assert scores == [0.1, 0.2, 0.3]


class TestStalenessIsMeasuredHonestly:
    """
    The property that makes the sweep meaningful.

    A delayed resolution genuinely serves an older snapshot, so its age is measured against
    the REAL moment. Measuring against the shifted one would make every delay look free —
    hiding the exact cost the sweep exists to measure.
    """

    def test_the_delay_ages_the_result(self, mock_logger):
        worker = build(mock_logger, 5, 10, at(8, 0, 0.1), at(8, 10, 0.2))
        assert worker.compute_signal_at(utc(2026, 1, 15, 8, 12)).is_stale is True

    def test_without_the_delay_the_same_moment_is_fresh(self, mock_logger):
        """Same archive, same moment, same threshold — only the lever differs."""
        worker = build(mock_logger, 0, 10, at(8, 0, 0.1), at(8, 10, 0.2))
        assert worker.compute_signal_at(utc(2026, 1, 15, 8, 12)).is_stale is False


class TestRefreshAgrees:
    """
    The refresh trigger and the resolution must read the same as-of moment.

    If they disagreed, the worker would either recompute against a snapshot it is not
    serving or hold a cached result past the window it belongs to — a drift that shows up
    as results that are correct individually and wrong in sequence.
    """

    def test_no_refresh_when_nothing_moved(self, mock_logger):
        worker = build(mock_logger, 5, 600, at(8, 0, 0.1), at(8, 10, 0.2))
        worker.compute_signal_at(utc(2026, 1, 15, 8, 11))
        assert worker.should_refresh_at(utc(2026, 1, 15, 8, 12)) is False

    def test_refresh_fires_on_the_delayed_window(self, mock_logger):
        """
        The 08:10 envelope becomes current at 08:15 with a five-minute delay — not at
        08:10, and not never.
        """
        worker = build(mock_logger, 5, 600, at(8, 0, 0.1), at(8, 10, 0.2))
        worker.compute_signal_at(utc(2026, 1, 15, 8, 11))
        assert worker.should_refresh_at(utc(2026, 1, 15, 8, 14)) is False
        assert worker.should_refresh_at(utc(2026, 1, 15, 8, 16)) is True
