"""
Breaking-news EDGE derivation (#141 Part 2a, Phase 4).

`is_breaking` is the state of one envelope. The edge is the transition between two
consecutively served envelopes — entered, exited, or nothing.

**It is derived on our side, in both pipelines.** The producer also offers a filtered
breaking-only view and we deliberately do not consume it: if the producer derived the
boundary live while we derived it in simulation, the two could drift and the disagreement
would be invisible — each side internally consistent, the pair silently wrong. Same rule as
the disturbance episodes (#451): a boundary is always derived from observed state; an
upstream declaration may contribute a label, never a boundary.

Most of what is pinned here is the three ways an edge must NOT fire. Each has a different
reason, and each would produce a transition that never happened.
"""

from types import SimpleNamespace

from conftest import SYMBOL, make_provider, snapshot, utc

from python.framework.types.signal_data_types import SentimentResult, SignalSnapshot
from python.framework.workers.core.llm_sentiment_worker import LlmSentimentWorker


def build(mock_logger, *snapshots) -> LlmSentimentWorker:
    """A sentiment worker over the given snapshots, already provided."""
    worker = LlmSentimentWorker(
        name='sentiment',
        parameters={'max_staleness_minutes': 600, 'signal_delay_minutes': 0},
        logger=mock_logger,
        trading_context=SimpleNamespace(symbol=SYMBOL))
    worker.set_signal_provider(make_provider(*snapshots))
    return worker


def breaking(hour, minute, is_breaking) -> SignalSnapshot:
    """A snapshot at a time, breaking or not."""
    return snapshot(utc(2026, 1, 15, hour, minute), score=0.5, confidence=0.9,
                    signal='BUY', urgency=0.8, is_breaking=is_breaking)


def with_evidence(hour, minute, is_breaking, evidence) -> SignalSnapshot:
    """A snapshot carrying an envelope-level evidence stamp (for the RC-4 case)."""
    return SignalSnapshot(
        collected_msc=utc(2026, 1, 15, hour, minute),
        schema_version='2.0',
        envelope_evidence_as_of=evidence,
        result=[SentimentResult(symbol=SYMBOL, signal='BUY', sentiment_score=0.5,
                                confidence=0.9, urgency=0.8, is_breaking=is_breaking,
                                evidence_as_of=evidence)])


class TestTransitions:
    """The two edges that are real."""

    def test_entering_a_breaking_state(self, mock_logger):
        worker = build(mock_logger, breaking(8, 0, False), breaking(8, 10, True))
        worker.compute_signal_at(utc(2026, 1, 15, 8, 1))
        result = worker.compute_signal_at(utc(2026, 1, 15, 8, 11))
        assert result.outputs['breaking_edge'] == 'entered'
        assert result.outputs['is_breaking'] is True

    def test_leaving_a_breaking_state(self, mock_logger):
        worker = build(mock_logger, breaking(8, 0, True), breaking(8, 10, False))
        worker.compute_signal_at(utc(2026, 1, 15, 8, 1))
        result = worker.compute_signal_at(utc(2026, 1, 15, 8, 11))
        assert result.outputs['breaking_edge'] == 'exited'

    def test_a_held_state_is_not_an_edge(self, mock_logger):
        """
        The distinction the whole output exists for: `is_breaking` stays true across a
        multi-envelope event, and only the first of them is an entry.
        """
        worker = build(mock_logger, breaking(8, 0, False), breaking(8, 10, True),
                       breaking(8, 20, True), breaking(8, 30, True))
        worker.compute_signal_at(utc(2026, 1, 15, 8, 1))
        assert worker.compute_signal_at(
            utc(2026, 1, 15, 8, 11)).outputs['breaking_edge'] == 'entered'
        assert worker.compute_signal_at(
            utc(2026, 1, 15, 8, 21)).outputs['breaking_edge'] == 'none'
        assert worker.compute_signal_at(
            utc(2026, 1, 15, 8, 31)).outputs['breaking_edge'] == 'none'

    def test_a_full_cycle(self, mock_logger):
        worker = build(mock_logger, breaking(8, 0, False), breaking(8, 10, True),
                       breaking(8, 20, False), breaking(8, 30, True))
        edges = [worker.compute_signal_at(utc(2026, 1, 15, 8, m)).outputs['breaking_edge']
                 for m in (1, 11, 21, 31)]
        assert edges == ['none', 'entered', 'exited', 'entered']


class TestNonEdges:
    """Three ways an edge must not fire, each for its own reason."""

    def test_the_first_envelope_never_enters(self, mock_logger):
        """
        A session booting into an active breaking event has witnessed no entry. Reporting
        one would make every restart during a running story look like a fresh event.
        """
        worker = build(mock_logger, breaking(8, 0, True))
        result = worker.compute_signal_at(utc(2026, 1, 15, 8, 1))
        assert result.outputs['is_breaking'] is True
        assert result.outputs['breaking_edge'] == 'none'

    def test_a_gap_is_unknown_not_false(self, mock_logger):
        """
        Reading a gap as 'not breaking' would emit an exit going in and an entry coming
        out — two transitions where the truth is simply that nothing was observed.
        """
        worker = build(mock_logger, breaking(8, 10, True))
        before = worker.compute_signal_at(utc(2026, 1, 15, 7, 0))
        assert before.outputs['breaking_edge'] == 'none'
        entered = worker.compute_signal_at(utc(2026, 1, 15, 8, 11))
        assert entered.outputs['breaking_edge'] == 'none'

    def test_a_gap_between_two_breaking_envelopes_reports_nothing(self, mock_logger):
        """The state is held across the gap, so recovery is not a new entry."""
        worker = build(mock_logger, breaking(8, 0, True), breaking(9, 0, True))
        worker.compute_signal_at(utc(2026, 1, 15, 8, 1))
        gap = worker.compute_signal_at(utc(2026, 1, 15, 7, 0))
        assert gap.outputs['breaking_edge'] == 'none'
        recovery = worker.compute_signal_at(utc(2026, 1, 15, 9, 1))
        assert recovery.outputs['breaking_edge'] == 'none'

    def test_an_overtaking_pass_does_not_flip_the_edge(self, mock_logger):
        """
        RC-4: an envelope resting on OLDER evidence did not witness what came after it.
        Letting it flip the edge would turn the producer's commit order into a phantom
        transition — exactly what the evidence-regression flag exists to prevent.
        """
        worker = build(
            mock_logger,
            with_evidence(8, 0, True, utc(2026, 1, 15, 7, 58)),
            with_evidence(8, 10, False, utc(2026, 1, 15, 7, 50)))
        worker.compute_signal_at(utc(2026, 1, 15, 8, 1))
        result = worker.compute_signal_at(utc(2026, 1, 15, 8, 11))
        assert result.outputs['evidence_regressed'] is True
        assert result.outputs['breaking_edge'] == 'none'

    def test_the_state_survives_an_overtaking_pass(self, mock_logger):
        """
        The suppressed envelope must not be remembered either, or the next correctly
        ordered envelope would compare against a view that was already discarded.
        """
        worker = build(
            mock_logger,
            with_evidence(8, 0, True, utc(2026, 1, 15, 7, 58)),
            with_evidence(8, 10, False, utc(2026, 1, 15, 7, 50)),
            with_evidence(8, 20, False, utc(2026, 1, 15, 8, 18)))
        worker.compute_signal_at(utc(2026, 1, 15, 8, 1))
        worker.compute_signal_at(utc(2026, 1, 15, 8, 11))
        result = worker.compute_signal_at(utc(2026, 1, 15, 8, 21))
        assert result.outputs['evidence_regressed'] is False
        assert result.outputs['breaking_edge'] == 'exited'


class TestContract:
    """The output as decisions subscribe to it."""

    def test_the_edge_is_a_declared_output(self):
        schema = LlmSentimentWorker.get_output_schema()
        assert 'breaking_edge' in schema
        assert schema['breaking_edge'].choices == ('entered', 'exited', 'none')

    def test_a_gap_result_still_carries_the_field(self, mock_logger):
        """A decision subscribing to it must never face a missing key."""
        worker = build(mock_logger, breaking(9, 0, False))
        assert 'breaking_edge' in worker.compute_signal_at(
            utc(2026, 1, 15, 7, 0)).outputs
