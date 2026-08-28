"""
FiniexTestingIDE - Signal Feed Contract Validator (netless half of #466)

The release-gate certificate runs against a live producer, which makes its assertions
expensive to develop and impossible to run in the daily suite. They are pure functions over
an envelope, though — so they can be exercised here against the frozen frame sample and,
more importantly, against DELIBERATELY BROKEN copies of it.

The second half is the point. A validator that has only ever seen a correct envelope is an
assertion nobody has watched fail, and every mismatch this project has had with the
producer was a check that could not fail: a loop over an empty set, a gate that read
presence but never type. Each negative case below is one of those mistakes, pinned.
"""

import copy
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from python.framework.types.signal_certificate_types import (
    FeedObservation,
    ProducerBuild,
)
from python.framework.types.signal_data_types import SignalSnapshot
from python.framework.validators.signal_feed_contract_validator import (
    SignalFeedContractValidator,
)

SAMPLE = Path('tests/fixtures/signals/signal_stream_frames_reissue7.sse')

# The receipt stamp the transport supplies. Absent on the wire by contract, so a fixed
# value here is not a simplification — it is what the consumer does.
RECEIPT_MSC = 1787311900000

# A populated id as the producer publishes it: free text in the middle segment, with
# spaces, a slash and further colons. Reissue 6 carries this exact string, but it stays
# pinned: the shape is the contract, and a constant keeps the negative cases below meaningful
# even against a future sample that happens not to contain a populated id.
PRODUCTION_EPISODE_ID = (
    'forex_macro_sentiment:US Dollar Canadian Dollar USD/CAD Bank of Canada '
    'BOC:2026-08-23T20:20:14Z')


def sample_envelopes() -> list:
    """
    Every `signal` frame's payload from the committed sample.

    Returns:
        The decoded envelopes, in file order
    """
    frames = []
    for block in SAMPLE.read_text(encoding='utf-8').split('\n\n'):
        lines = [line for line in block.splitlines() if line.strip()]
        if not any(line.strip() == 'event: signal' for line in lines):
            continue
        for line in lines:
            if line.startswith('data: '):
                frames.append(json.loads(line[6:]))
    return frames


def observe(envelope: dict) -> FeedObservation:
    """
    Wrap one raw envelope the way the live observer would.

    Args:
        envelope: The raw decoded payload

    Returns:
        The observation the validator takes
    """
    return FeedObservation(
        envelope=envelope,
        snapshot=SignalSnapshot.model_validate(
            {**envelope, 'collected_msc': RECEIPT_MSC}),
        fetched_at=datetime.fromtimestamp(RECEIPT_MSC / 1000.0, tz=timezone.utc),
        frame_bytes=len(json.dumps(envelope).encode('utf-8')))


def wire_failures(envelope: dict) -> dict:
    """
    Failed WIRE-shape checks for one envelope, keyed by name.

    Reads the raw payload only, so it works on envelopes our reader refuses — which is the
    case that matters most: a wrongly typed field makes the model raise, and a certificate
    that stopped there would report 'the reader said no' without naming the field.

    Args:
        envelope: The raw decoded payload

    Returns:
        Mapping of check name to its detail
    """
    checks = SignalFeedContractValidator().validate_wire_shape(envelope)
    return {c.name: c.detail for c in checks if not c.ok}


def full_failures(envelope: dict) -> dict:
    """
    Failed checks including our reader's own guarantees. Requires the envelope to parse.

    Args:
        envelope: The raw decoded payload

    Returns:
        Mapping of check name to its detail
    """
    checks = SignalFeedContractValidator().validate_envelope(observe(envelope))
    return {c.name: c.detail for c in checks if not c.ok}


class TestTheSamplePasses:
    """The frozen sample satisfies the contract the certificate asserts."""

    def test_the_sample_is_present(self):
        assert SAMPLE.exists(), f'the frozen frame sample is missing: {SAMPLE}'
        assert sample_envelopes(), 'no signal frames in the sample'

    def test_every_sample_envelope_holds_the_contract(self):
        """
        The whole check list over the wire sample, with nothing mocked.

        If this goes red after a reissue, the reissue changed the contract — which is
        exactly the signal the frozen sample exists to give.
        """
        for envelope in sample_envelopes():
            assert not full_failures(envelope), (
                f"seq {envelope.get('seq')}: {full_failures(envelope)}")

    def test_the_sample_exercises_the_evidence_comparison(self):
        """
        Guards the assertion above against passing vacuously.

        `evidence_matches_max_fetched_at` can only fail on rows that carry BOTH a stamp and
        sources. If a future sample carried neither, the check would report success while
        comparing nothing — so the sample's own coverage is asserted here.
        """
        compared = sum(
            1 for envelope in sample_envelopes() for row in envelope.get('result') or []
            if row.get('evidence_as_of') is not None and row.get('sources'))
        assert compared > 0, (
            'no sample row carries both an evidence stamp and sources, so the evidence '
            'comparison would pass without comparing anything')


class TestTheValidatorCanFail:
    """Each negative case is a mistake this project actually made."""

    @pytest.fixture
    def envelope(self):
        """One good envelope to break in a controlled way."""
        return copy.deepcopy(sample_envelopes()[0])

    def test_a_timestamp_in_the_episode_start_flag_is_refused(self, envelope):
        """
        Our own bug, and the most expensive one: it is a FLAG, not a timestamp.

        We declared it as a datetime, every live envelope from their deploy onward was
        rejected, and the rejection was filed as the producer's outage.
        """
        envelope['result'][0]['breaking_episode_start'] = '2026-08-24T21:05:00Z'
        assert 'row_field_breaking_episode_start' in wire_failures(envelope)

    def test_an_integer_in_a_boolean_field_is_refused(self, envelope):
        """
        `is_breaking: 1` must not pass as a flag.

        Python makes bool a subclass of int, so the lenient isinstance check accepts this —
        and the producer's own hardened gate refuses it, so ours must too.
        """
        envelope['result'][0]['is_breaking'] = 1
        assert 'row_field_is_breaking' in wire_failures(envelope)

    def test_a_string_in_a_numeric_field_is_refused(self, envelope):
        """`urgency: "0.8"` — the other half of the producer's four verified cases."""
        envelope['result'][0]['urgency'] = '0.8'
        assert 'row_field_urgency' in wire_failures(envelope)

    def test_a_boolean_in_a_numeric_field_is_refused(self, envelope):
        """The mirror of the integer case: True must not pass as a score."""
        envelope['result'][0]['confidence'] = True
        assert 'row_field_confidence' in wire_failures(envelope)

    def test_trigger_reason_only_in_metadata_is_refused(self, envelope):
        """
        The producer spent a schema major on promoting it out of metadata.

        Our reader still tolerates the old location for archived lines, which is right —
        but a LIVE envelope carrying it only there means the contract regressed.
        """
        envelope['metadata']['trigger_reason'] = envelope.pop('trigger_reason')
        assert 'trigger_reason_at_top_level' in wire_failures(envelope)

    def test_collected_msc_on_the_wire_is_refused(self, envelope):
        """Two different instants must not share one field name."""
        envelope['collected_msc'] = RECEIPT_MSC
        assert 'collected_msc_absent_on_wire' in wire_failures(envelope)

    def test_evidence_after_availability_is_refused(self, envelope):
        """Evidence the producer had not retrieved yet cannot have informed the pass."""
        envelope['result'][0]['evidence_as_of'] = envelope['available_msc'] + 1
        assert 'no_evidence_after_available_msc' in wire_failures(envelope)

    def test_an_evidence_stamp_that_disagrees_with_its_sources_is_refused(self, envelope):
        """The stamp must be the newest fetched_at, not merely a plausible number."""
        envelope['result'][0]['evidence_as_of'] -= 60_000
        assert 'evidence_matches_max_fetched_at' in wire_failures(envelope)

    def test_a_stamp_without_evidence_is_refused(self, envelope):
        """Present exactly when the row rests on something."""
        envelope['result'][0]['sources'] = []
        assert 'evidence_present_exactly_with_evidence' in wire_failures(envelope)

    def test_an_unsupported_schema_major_is_refused(self, envelope):
        """A major means a breaking change, so reading on would be reading blind."""
        envelope['schema_version'] = '9.0'
        assert 'schema_major_supported' in wire_failures(envelope)

    def test_an_absent_contracted_field_is_refused(self, envelope):
        """Every Tier 1-3 field is unconditional on the wire."""
        del envelope['config_fingerprint']
        assert 'envelope_field_config_fingerprint' in wire_failures(envelope)


class TestEpisodeOpacity:
    """The id is opaque, and the check says so in both directions."""

    @pytest.fixture
    def envelope(self):
        return copy.deepcopy(sample_envelopes()[0])

    def test_the_production_form_passes(self, envelope):
        """
        A real published id, with spaces and a slash in its middle segment.

        The mock generator keys on the base currency and produces a much narrower string,
        so calibrating anything on the sample alone would test the easy case.
        """
        envelope['result'][0]['breaking_episode_id'] = PRODUCTION_EPISODE_ID
        envelope['result'][0]['breaking_episode_start'] = True
        assert 'episode_id_is_opaque_when_populated' not in wire_failures(envelope)

    def test_a_cleanly_splittable_id_is_refused(self, envelope):
        """
        An id that splits into exactly its three contracted segments.

        It would tempt code into splitting on ':' — which then breaks on the next query
        text, because the real middle segment is free-form pipeline config.
        """
        envelope['result'][0]['breaking_episode_id'] = 'pipeline:BTC:opened'
        assert 'episode_id_is_opaque_when_populated' in wire_failures(envelope)

    def test_an_opener_without_an_id_is_refused(self, envelope):
        """A pass that raises the flag is inside an episode, so it must name it."""
        envelope['result'][0]['breaking_episode_start'] = True
        envelope['result'][0]['breaking_episode_id'] = None
        assert 'episode_start_implies_an_id' in wire_failures(envelope)

    def test_an_absent_id_is_reported_as_unexercised(self, envelope):
        """
        An envelope with no populated id must SAY the check went unexercised, not pass.

        A loop over an empty set is an assertion that cannot fail; naming it keeps the
        certificate honest about what it did NOT verify. The absent case is constructed here
        rather than inherited from the sample: reissue 5 happened to carry no populated id
        and reissue 6 carries two, so a test resting on that property tests the fixture
        rather than the validator.
        """
        for row in envelope['result']:
            row['breaking_episode_id'] = None
            row['breaking_episode_start'] = False
        checks = SignalFeedContractValidator().validate_envelope(observe(envelope))
        opacity = next(c for c in checks
                       if c.name == 'episode_id_is_opaque_when_populated')
        assert opacity.ok and 'not exercised' in opacity.detail


class TestTheReaderTier:
    """What our own reader guarantees, as opposed to what the wire declares."""

    @pytest.fixture
    def envelope(self):
        return copy.deepcopy(sample_envelopes()[0])

    def test_an_unknown_vocabulary_value_is_tolerated(self, envelope):
        """
        The reader must keep working when the producer adds a value.

        Asserted by mutation: signal / basis / status / data_origin are replaced with
        something no producer will ever emit, and the envelope must still parse.
        """
        assert 'closed_vocabulary_values_tolerated' not in full_failures(envelope)

    def test_a_wrongly_typed_flag_is_refused_by_the_reader_but_still_named(self, envelope):
        """
        The two tiers doing their separate jobs — and the reason they are separate.

        A flag typed as a timestamp makes the MODEL raise, so the reader-tier check fails
        with 'refused'. On its own that is the report we already lived through: our schema
        reading as their outage. The wire tier runs over the same raw payload regardless
        and names the field, which turns 'something is wrong' into a diagnosis.
        """
        envelope['result'][0]['breaking_episode_start'] = '2026-08-24T21:05:00Z'
        with pytest.raises(Exception):
            SignalSnapshot.model_validate({**envelope, 'collected_msc': RECEIPT_MSC})
        assert 'row_field_breaking_episode_start' in wire_failures(envelope)


class TestBuildProvenance:
    """Whose code produced the envelopes, and whose read them."""

    def _producer(self, build: ProducerBuild, release: str = 'dev'):
        """Producer-side verdict for one build document."""
        checks = SignalFeedContractValidator().validate_build(
            build=build, consumer_dirty=False, consumer_uncommitted=0,
            release_version=release)
        return next(c for c in checks if c.name == 'producer_build_is_reproducible')

    def _consumer(self, dirty, uncommitted: int, release: str):
        """Our-side verdict for one working-tree state."""
        checks = SignalFeedContractValidator().validate_build(
            build=ProducerBuild(offered=True, commit='52a9219', dirty=False),
            consumer_dirty=dirty, consumer_uncommitted=uncommitted,
            release_version=release)
        return next(c for c in checks if c.name == 'consumer_build_is_committed')

    def test_a_clean_published_build_passes(self):
        """The normal case, measured live: version 0.3.3 at commit 52a9219, built clean."""
        check = self._producer(ProducerBuild(
            offered=True, version='0.3.3', commit='52a9219', dirty=False))
        assert check.ok and '52a9219' in check.detail

    def test_an_unpublished_build_is_not_asserted(self):
        """
        Their route sits behind a switch, so its absence is a policy answer.

        Asserting against a promise nobody made is how a certificate starts reporting
        their configuration as our failure.
        """
        check = self._producer(ProducerBuild(offered=False, detail='HTTP 404 — Not Found'))
        assert check.ok and 'not asserted' in check.detail

    def test_a_published_build_without_a_commit_fails(self):
        """A build document that cannot name its code is not a build identity."""
        assert not self._producer(ProducerBuild(offered=True, version='0.3.3')).ok

    def test_a_dirty_producer_build_fails(self):
        """Envelopes from an uncommitted tree cannot be re-derived on their side either."""
        assert not self._producer(ProducerBuild(
            offered=True, version='0.3.3', commit='52a9219', dirty=True)).ok

    def test_our_clean_tree_passes_either_way(self):
        for release in ('dev', '1.4'):
            assert self._consumer(False, 0, release).ok

    def test_our_dirty_tree_is_recorded_during_a_rehearsal(self):
        """
        A working tree is the normal case during development; the gate must not go red.

        The state is still written into the detail, so the artifact never hides it.
        """
        check = self._consumer(True, 19, 'dev')
        assert check.ok and '19 uncommitted' in check.detail

    def test_our_dirty_tree_fails_a_declared_release(self):
        """
        An artifact CLAIMING a version whose code exists only in one working tree cannot be
        re-derived by anyone, including us.
        """
        check = self._consumer(True, 19, '1.4')
        assert not check.ok and '1.4' in check.detail

    def test_an_unreadable_git_fails_a_declared_release_only(self):
        """Not knowing which code ran is the same problem as knowing it was uncommitted."""
        assert self._consumer(None, 0, 'dev').ok
        assert not self._consumer(None, 0, '1.4').ok


class TestSeriesAcrossCertificates:
    """The rewind comparison, which needs no producer at all."""

    def test_the_first_certificate_establishes_the_binding(self):
        checks = SignalFeedContractValidator().validate_against_previous(
            '138c68e48b15', 493, None)
        assert all(c.ok for c in checks)
        assert any('ESTABLISHES' in c.detail for c in checks)

    def test_a_lower_seq_on_the_same_journal_is_a_rewind(self):
        """
        The producer's own near-miss: a wipe truncated the sequence AND the journal.

        Boot reconciliation recovers a reset counter by reading max(seq) back out of the
        journal, so with both gone the engine re-mints from 1 while a consumer holds a much
        higher cursor — and the connection stays perfectly healthy.
        """
        previous = {'timestamp': '2026-08-25T10:00:00+00:00',
                    'producer': {'journal_id': '138c68e48b15'},
                    'series': {'seq_last': 493}}
        checks = SignalFeedContractValidator().validate_against_previous(
            '138c68e48b15', 1, previous)
        failed = {c.name for c in checks if not c.ok}
        assert failed == {'seq_did_not_rewind_since_last_certificate'}

    def test_a_restart_between_two_certificates_is_named(self):
        """
        A restart is the one moment a counter can be re-minted, so it belongs in the detail.

        Measured on 2026-08-25: the producer restarted at 16:28:23 between two of our
        certificates and the sequence continued cleanly (498 → 499), which is their boot
        reconciliation recovering the counter from the journal. Naming the restart is what
        makes that observation legible instead of invisible.
        """
        previous = {'timestamp': '2026-08-25T16:22:08+00:00',
                    'producer': {'journal_id': '138c68e48b15'},
                    'series': {'seq_last': 498},
                    'build': {'producer': {
                        'started_at': '2026-08-25T15:10:00+00:00'}}}
        build = ProducerBuild(
            offered=True, commit='52a9219',
            started_at=datetime(2026, 8, 25, 16, 28, 23, tzinfo=timezone.utc))
        checks = SignalFeedContractValidator().validate_against_previous(
            '138c68e48b15', 499, previous, build)
        rewind = next(c for c in checks
                      if c.name == 'seq_did_not_rewind_since_last_certificate')
        assert rewind.ok and 'RESTART' in rewind.detail

    def test_one_continuous_process_is_named_too(self):
        """The other outcome is worth stating: nothing could have been re-minted."""
        started = datetime(2026, 8, 25, 16, 28, 23, tzinfo=timezone.utc)
        previous = {'timestamp': '2026-08-25T16:30:00+00:00',
                    'producer': {'journal_id': '138c68e48b15'},
                    'series': {'seq_last': 500},
                    'build': {'producer': {'started_at': started.isoformat()}}}
        checks = SignalFeedContractValidator().validate_against_previous(
            '138c68e48b15', 501, previous,
            ProducerBuild(offered=True, commit='52a9219', started_at=started))
        rewind = next(c for c in checks
                      if c.name == 'seq_did_not_rewind_since_last_certificate')
        assert rewind.ok and 'same producer process' in rewind.detail

    def test_a_different_journal_is_a_finding(self):
        """
        The binding is falsifiable by comparison rather than by trust.

        Both fingerprints were measured minutes apart on 2026-08-24 and the seq ranges
        overlap, so only the journal separates a development run from a production one.
        """
        previous = {'timestamp': '2026-08-25T10:00:00+00:00',
                    'producer': {'journal_id': '138c68e48b15'},
                    'series': {'seq_last': 164}}
        checks = SignalFeedContractValidator().validate_against_previous(
            '9c3fa4c80d95', 493, previous)
        failed = {c.name for c in checks if not c.ok}
        assert failed == {'journal_matches_previous_certificate'}
