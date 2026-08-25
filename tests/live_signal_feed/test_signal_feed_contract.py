"""
FiniexTestingIDE - Signal Feed Contract Conformance (#466)

The substance of the certificate, and the part that is transport-independent by
construction: every assertion here takes an envelope and does not care whether it arrived
over the interim pull transport or the stream (#468). The same tests certify both.

What this proves: the envelopes are readable, correctly shaped and honestly stamped. What
it deliberately does NOT prove: that the sentiment is correct. That is unknowable, and a
gate that turns red on an unlucky news day certifies nothing but the weather.
"""

from tests.live_signal_feed.signal_feed_assertions import assert_check, assert_group


class TestEnvelopeShape:
    """The declared shape, at its declared locations, with its declared types."""

    def test_the_schema_major_is_supported(self, assessment):
        """
        The major is pinned, the minor only recorded.

        From the producer's #65 note onward a minor means an additive field and a major a
        breaking one — so a minor we have not seen means the shape grew, which is not a
        failure.
        """
        assert_check(assessment, 'schema_major_supported')

    def test_trigger_reason_sits_at_the_top_level(self, assessment):
        """
        Not in metadata. The producer spent a schema major on that move.

        It is also the only way to tell a scheduled pass from an out-of-band one — timing
        cannot, because the envelope is stamped at the end of a variable-length run.
        """
        assert_check(assessment, 'trigger_reason_at_top_level')

    def test_collected_msc_is_absent_on_the_wire(self, assessment):
        """
        The receive stamp is OURS to set.

        If the producer sent one, two different instants would share a field name — and
        the no-look-ahead gate resolves against that name.
        """
        assert_check(assessment, 'collected_msc_absent_on_wire')

    def test_every_envelope_field_is_present_and_typed(self, assessment):
        """
        Presence, location AND type.

        The type is the half their own frame-sample gate omitted, which is how a `''`
        placeholder passed it three days before production began emitting `null`. A
        certificate repeating that omission would certify the same blind spot.
        """
        assert_group(assessment, 'envelope_field_')

    def test_every_row_field_is_present_and_typed(self, assessment):
        """
        Same rule per symbol row, with bool and int held apart.

        Python makes bool a subclass of int, so a lenient check would accept
        `is_breaking: 1` for a flag — exactly the shape the producer's hardened gate now
        refuses.
        """
        assert_group(assessment, 'row_field_')


class TestStamps:
    """The evidence stamps say something true about the evidence."""

    def test_evidence_matches_the_newest_source(self, assessment):
        """
        Each row's `evidence_as_of` equals the newest `fetched_at` it rests on.

        Compared at millisecond resolution, which is the producer's own precision — their
        stamp is truncated while a source's carries microseconds.
        """
        assert_check(assessment, 'evidence_matches_max_fetched_at')

    def test_evidence_is_present_exactly_when_there_is_evidence(self, assessment):
        """
        A row resting on nothing carries no stamp, and a row resting on something does.

        When no row in the envelope lacks evidence, the certificate records
        `rows_without_evidence: 0` so the unexercised branch is visible rather than
        silently green.
        """
        assert_check(assessment, 'evidence_present_exactly_with_evidence')

    def test_no_evidence_is_stamped_after_availability(self, assessment):
        """
        Evidence the producer had not retrieved yet cannot have informed the pass.

        A later stamp would be a look-ahead on their side, which our own no-look-ahead
        gate could not catch.
        """
        assert_check(assessment, 'no_evidence_after_available_msc')


class TestTolerance:
    """The reader survives what it does not recognize."""

    def test_an_unknown_vocabulary_value_is_tolerated(self, assessment):
        """
        Proven by mutation, not by assumption.

        The observed envelope is re-read with a signal / basis / status / data_origin no
        producer will ever emit, and the reader must still parse it. Asserting this against
        only the values that happen to arrive would be an assertion that cannot fail.
        """
        assert_check(assessment, 'closed_vocabulary_values_tolerated')


class TestEpisodeIdentity:
    """The field group all three of our contract mismatches were in."""

    def test_the_episode_id_is_opaque(self, assessment):
        """
        Never split it. The middle segment is free-text pipeline config.

        A populated id reads like three colon-separated segments and is not: the production
        form carries spaces, a slash and further colons. What it guarantees is byte
        equality — same story, same value.
        """
        assert_check(assessment, 'episode_id_is_opaque_when_populated')

    def test_an_opener_names_its_episode(self, assessment):
        """
        `breaking_episode_start` is a FLAG, and a pass that raises it is inside an episode.

        Typing that flag as a timestamp is the mistake that made us reject every live
        envelope for a day and file it as the producer's outage.
        """
        assert_check(assessment, 'episode_start_implies_an_id')


class TestReaderContract:
    """Our production reader, unmodified."""

    def test_the_envelope_parses_through_the_production_reader(self, assessment):
        """No test-only shim — that it goes through the shipped model IS the assertion."""
        assert_check(assessment, 'envelope_parses_through_production_reader')

    def test_the_resolution_key_is_the_availability_stamp(self, assessment):
        """
        `available_msc`, the honest publish instant, identical in every copy.

        Not our receive time: two consumers reading the same envelope must resolve it at
        the same moment, or a backtest and a live session disagree about when a decision
        could have been made.
        """
        assert_check(assessment, 'resolution_key_is_available_msc')

    def test_the_order_key_is_well_formed(self, assessment):
        """
        `(stream_epoch, seq)` — a total chronological order with no clock in it.
        """
        assert_check(assessment, 'order_key_is_well_formed')

    def test_nothing_resolves_before_availability(self, assessment):
        """
        The no-look-ahead gate, exercised through the production SignalDataProvider.

        One second before the availability stamp the provider must return nothing.
        """
        assert_check(assessment, 'no_look_ahead_before_availability')
