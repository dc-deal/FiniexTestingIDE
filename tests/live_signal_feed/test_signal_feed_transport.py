"""
FiniexTestingIDE - Signal Feed Transport & Provenance (#466)

The thin half of the certificate: did we reach the producer, was the credential accepted,
which store answered — and did the run stay free.

The transport section is deliberately small because it is the part the stream (#468)
replaces. Everything of substance is in the contract tests beside it, which take an
envelope and do not care where it came from.
"""

from tests.live_signal_feed.signal_feed_assertions import assert_check


class TestTransport:
    """The producer answered, and the run cost nothing."""

    def test_the_address_answers(self, assessment):
        """
        /v1/health without a token: a failure here is the ADDRESS, not the credential.

        The producer documents it as their one open route, so probing it unauthenticated
        is what separates the two failure modes instead of merging them into 'unreachable'.
        """
        assert_check(assessment, 'health_route_answers')

    def test_the_credential_is_accepted(self, assessment):
        """
        A 401 is a credential condition, never an outage — their contract says so.

        Reported separately so a revoked token never sends the operator looking at the
        producer's system.
        """
        assert_check(assessment, 'credential_accepted')

    def test_an_envelope_was_served(self, assessment):
        """The source answered with something to certify."""
        assert_check(assessment, 'latest_route_answers')

    def test_the_run_spent_nothing(self, assessment):
        """
        Only the producer's free GET routes were called.

        On a producer that spends real money per pass this is a certifiable property, and
        asserting it against the RECORDED calls is what keeps a later 'let us make the
        test more thorough' from quietly buying LLM calls.
        """
        assert_check(assessment, 'run_touched_only_free_routes')


class TestProvenance:
    """Which producer answered, and was its data real."""

    def test_the_producer_named_a_journal(self, assessment):
        """
        A session against no identifiable series is not a series anything can certify.

        `journal_id: null` is a real answer and not a probe failure — the producer has no
        store attached, or cannot read its own identifier. Either way it is a FAIL.
        """
        assert_check(assessment, 'producer_named_a_journal')

    def test_the_journal_is_not_a_development_instance(self, assessment):
        """
        The two instances share a schema, a pipeline_id and a seq range.

        Nothing on an envelope says which store it came from, so a certificate signed
        against the development engine would pass and certify nothing. This is the
        name-level guard; the binding guard is the fingerprint chain in the series tests.
        """
        assert_check(assessment, 'journal_is_not_a_development_instance')

    def test_the_run_was_aimed_at_production(self, assessment):
        """
        Which instance we AIMED at, beside which one answered.

        Intent and answer are two different facts, and a certificate that records only the
        second cannot show a misconfiguration that happened to reach the right place.
        """
        assert_check(assessment, 'endpoint_aimed_at_production')

    def test_the_producer_build_is_reproducible(self, assessment):
        """
        WHICH CODE produced these envelopes, not just which version string.

        Measured 2026-08-25: the producer deployed a new commit while `version` stayed
        '0.3.3', so two certificates taken twenty minutes apart came from different code and
        looked identical. Only the commit binds — the same relationship `journal_id` has to
        the environment name.

        Their build route is public by default but sits behind a switch on their side, so
        'not offered' passes and says so rather than reporting their policy as our failure.
        """
        assert_check(assessment, 'producer_build_is_reproducible')

    def test_our_own_build_is_committed(self, assessment):
        """
        The other half: a certificate must name code somebody else can check out.

        Asserted only when a release version was actually declared — a rehearsal against a
        working tree is the normal case during development. But an artifact CLAIMING a
        version whose code exists only in one working tree cannot be re-derived by anyone,
        including us.
        """
        assert_check(assessment, 'consumer_build_is_committed')

    def test_the_data_is_live(self, assessment):
        """
        `data_origin == 'live'`.

        A release certificate signed against synthetic data is worse than no certificate,
        because it looks like proof. Their mock generator emits 'synthetic' precisely so
        this can be caught.
        """
        assert_check(assessment, 'data_origin_is_live')
