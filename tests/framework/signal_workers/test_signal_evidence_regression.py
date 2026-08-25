"""
RC-4: the producer's passes can overtake each other (#141 Part 2a).

The producer runs passes concurrently, so a long-running pass commits AFTER a later one — it
carries the newer position in the series and the older view of the world. A decision reading that
as a CHANGE reacts to a reversal that happened only in the ordering.

Two things decide whether this works, and both are counter-intuitive enough to pin:

1. **The comparison is per ENVELOPE, never per row.** A row's evidence stamp may legitimately fall
   between passes (its retrieved set changes), so a per-row comparison reports a regression
   constantly — measured on one mock week: 2073 per row against 17 per envelope.
2. **The runtime series is PROJECTED to one symbol**, so a projected snapshot holds one row and a
   max over its rows is that row's stamp. Without the envelope value carried alongside, simulation
   and live would disagree — measured on the same week: 237 against 17.
"""

from conftest import SYMBOL, make_provider, utc

from python.framework.types.signal_data_types import SentimentResult, SignalSnapshot


def envelope(collected, rows, envelope_evidence=None) -> SignalSnapshot:
    """
    A snapshot with per-row evidence stamps.

    Args:
        collected: Receive stamp
        rows: (symbol, evidence datetime or None) pairs
        envelope_evidence: Envelope-level value, as the importer writes it
    """
    return SignalSnapshot(
        collected_msc=collected,
        schema_version='2.0',
        envelope_evidence_as_of=envelope_evidence,
        result=[SentimentResult(symbol=sym, signal='HOLD', evidence_as_of=ev)
                for sym, ev in rows],
    )


class TestEnvelopeEvidence:
    """What `get_evidence_as_of` answers, and why the unit matters."""

    def test_complete_envelope_takes_the_row_maximum(self):
        """On the wire every row is present, so the max over rows IS the envelope's."""
        snap = envelope(utc(2026, 1, 15, 8, 0), [
            (SYMBOL, utc(2026, 1, 15, 7, 55)),
            ('ETHUSD', utc(2026, 1, 15, 7, 58)),
        ])
        assert snap.get_evidence_as_of() == utc(2026, 1, 15, 7, 58)

    def test_carried_envelope_value_wins_over_the_rows(self):
        """
        A projected snapshot holds ONE row, so its max is that row's stamp. The importer
        carries the true envelope value alongside; it must take precedence.
        """
        snap = envelope(
            utc(2026, 1, 15, 8, 0),
            [(SYMBOL, utc(2026, 1, 15, 7, 55))],
            envelope_evidence=utc(2026, 1, 15, 7, 58))
        assert snap.get_evidence_as_of() == utc(2026, 1, 15, 7, 58)

    def test_no_evidence_at_all(self):
        """The status='error' case: an empty result has nothing to stamp."""
        assert envelope(utc(2026, 1, 15, 8, 0), []).get_evidence_as_of() is None

    def test_rows_without_evidence_are_ignored(self):
        """basis='no_data' rows rest on nothing and must not drag the maximum down."""
        snap = envelope(utc(2026, 1, 15, 8, 0), [
            (SYMBOL, None),
            ('ETHUSD', utc(2026, 1, 15, 7, 58)),
        ])
        assert snap.get_evidence_as_of() == utc(2026, 1, 15, 7, 58)


class TestRegressionDetection:
    """The flag a decision reads to tell a change from an overtaking pass."""

    def _worker(self, mock_logger, *snapshots):
        """A worker over the given snapshots, already provided."""
        from types import SimpleNamespace

        from python.framework.workers.core.llm_sentiment_worker import LlmSentimentWorker
        worker = LlmSentimentWorker(
            name='sentiment', parameters={'max_staleness_minutes': 600, 'signal_delay_minutes': 0},
            logger=mock_logger, trading_context=SimpleNamespace(symbol=SYMBOL))
        worker.set_signal_provider(make_provider(*snapshots))
        return worker

    def test_advancing_evidence_is_not_a_regression(self, mock_logger):
        worker = self._worker(
            mock_logger,
            envelope(utc(2026, 1, 15, 8, 0), [(SYMBOL, utc(2026, 1, 15, 7, 55))]),
            envelope(utc(2026, 1, 15, 8, 10), [(SYMBOL, utc(2026, 1, 15, 8, 5))]))
        worker.compute_signal_at(utc(2026, 1, 15, 8, 1))
        result = worker.compute_signal_at(utc(2026, 1, 15, 8, 11))
        assert result.outputs['evidence_regressed'] is False

    def test_overtaking_pass_is_flagged(self, mock_logger):
        """The later envelope rests on OLDER evidence — RC-4."""
        worker = self._worker(
            mock_logger,
            envelope(utc(2026, 1, 15, 8, 0), [(SYMBOL, utc(2026, 1, 15, 7, 58))]),
            envelope(utc(2026, 1, 15, 8, 10), [(SYMBOL, utc(2026, 1, 15, 7, 52))]))
        worker.compute_signal_at(utc(2026, 1, 15, 8, 1))
        result = worker.compute_signal_at(utc(2026, 1, 15, 8, 11))
        assert result.outputs['evidence_regressed'] is True

    def test_the_first_envelope_is_never_a_regression(self, mock_logger):
        worker = self._worker(
            mock_logger,
            envelope(utc(2026, 1, 15, 8, 0), [(SYMBOL, utc(2026, 1, 15, 7, 55))]))
        result = worker.compute_signal_at(utc(2026, 1, 15, 8, 1))
        assert result.outputs['evidence_regressed'] is False

    def test_missing_evidence_does_not_flag(self, mock_logger):
        """An envelope resting on nothing says nothing about ordering."""
        worker = self._worker(
            mock_logger,
            envelope(utc(2026, 1, 15, 8, 0), [(SYMBOL, utc(2026, 1, 15, 7, 55))]),
            envelope(utc(2026, 1, 15, 8, 10), [(SYMBOL, None)]))
        worker.compute_signal_at(utc(2026, 1, 15, 8, 1))
        result = worker.compute_signal_at(utc(2026, 1, 15, 8, 11))
        assert result.outputs['evidence_regressed'] is False

    def test_the_flag_clears_on_the_next_advance(self, mock_logger):
        """It marks the envelope, not the session."""
        worker = self._worker(
            mock_logger,
            envelope(utc(2026, 1, 15, 8, 0), [(SYMBOL, utc(2026, 1, 15, 7, 58))]),
            envelope(utc(2026, 1, 15, 8, 10), [(SYMBOL, utc(2026, 1, 15, 7, 52))]),
            envelope(utc(2026, 1, 15, 8, 20), [(SYMBOL, utc(2026, 1, 15, 8, 15))]))
        worker.compute_signal_at(utc(2026, 1, 15, 8, 1))
        assert worker.compute_signal_at(
            utc(2026, 1, 15, 8, 11)).outputs['evidence_regressed'] is True
        assert worker.compute_signal_at(
            utc(2026, 1, 15, 8, 21)).outputs['evidence_regressed'] is False

    def test_a_gap_does_not_flag(self, mock_logger):
        """Nothing resolvable is not an ordering statement."""
        worker = self._worker(
            mock_logger,
            envelope(utc(2026, 1, 15, 8, 0), [(SYMBOL, utc(2026, 1, 15, 7, 55))]))
        result = worker.compute_signal_at(utc(2026, 1, 15, 7, 0))
        assert result.outputs['evidence_regressed'] is False
