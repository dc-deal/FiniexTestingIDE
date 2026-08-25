"""
FiniexTestingIDE - Signal Feed Series Integrity (#466)

Two questions a single read cannot answer: did the series behave DURING this run, and did
it behave BETWEEN this run and the last certificate.

The second is the check only this artifact can make. A producer-side rewind is invisible
from inside one session — nothing steps backwards because nothing arrives at all, and the
staleness contract then reports 'the producer went quiet': the correct symptom with the
wrong diagnosis. The certificate is the only thing that survives between runs.
"""

from tests.live_signal_feed.signal_feed_assertions import assert_check


class TestWithinTheRun:
    """The stream position behaved across this session's observations."""

    def test_seq_never_steps_backwards(self, assessment):
        """The producer's per-pipeline counter only moves forward within an epoch."""
        assert_check(assessment, 'seq_never_steps_backwards')

    def test_the_stream_epoch_held(self, assessment):
        """
        An epoch change mid-run invalidates the cursor built against the previous one.

        It changes only when the producer's series was reset, so seeing one inside a
        certificate run means the run straddled that reset.
        """
        assert_check(assessment, 'stream_epoch_stable_within_run')

    def test_the_producer_cadence_matches_what_we_registered(self, assessment):
        """
        Their reported interval against our configured one.

        Ours drives the staleness threshold, so a producer that slowed down turns a healthy
        feed into one that keeps tripping the contract — and one that sped up hides a
        genuine outage inside the tolerance.
        """
        assert_check(assessment, 'producer_cadence_matches_registered')


class TestAcrossCertificates:
    """The comparison that needs an artifact from a previous run."""

    def test_the_journal_matches_the_previous_certificate(self, assessment):
        """
        The binding is falsifiable by comparison rather than by trust.

        The first certificate against production establishes which fingerprint means
        production; every later one is checked against it. A mismatch is a finding, not a
        formatting difference.
        """
        assert_check(assessment, 'journal_matches_previous_certificate')

    def test_the_series_did_not_rewind(self, assessment):
        """
        A lower seq on the same journal than the last certificate recorded.

        The producer's own near-miss: a 'clean slate' wipe truncated the sequence AND the
        journal, and boot reconciliation recovers a reset counter by reading max(seq) back
        out of the journal — so with both gone the engine re-mints from seq 1 while a
        consumer holds a much higher cursor. Every new frame then sits below the mark and
        is ignored, and the connection stays perfectly healthy.
        """
        assert_check(assessment, 'seq_did_not_rewind_since_last_certificate')
