"""
FiniexTestingIDE - Signal Feed Contract Validator
The assertions behind the live signal feed release gate (#466).

Pure judgment over data already read: every method takes envelopes and returns verdicts,
and none of them opens a connection. That is the whole point of the cut — the contract
checks do not care where an envelope came from, so the interim pull transport and the
stream (#468) are certified by the same assertions, and the offline half runs against the
frozen frame sample with no network at all.

What it certifies: that the producer's envelopes are readable, correctly shaped and
honestly stamped. What it must NOT certify: that the sentiment is correct. That is
unknowable, and a gate that turns red on an unlucky news day certifies nothing but the
weather.
"""

from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

from python.framework.signal_data.signal_data_provider import SignalDataProvider
from python.framework.types.signal_certificate_types import (
    FeedCheck,
    FeedObservation,
    FeedProbeResult,
    ProducerBuild,
)
from python.framework.types.signal_data_types import (
    SUPPORTED_SCHEMA_MAJORS,
    SentimentResult,
    SignalSeries,
    SignalSnapshot,
    schema_major,
)

# Envelope-level fields the contract puts at the top level, with the type each must carry.
# The TYPE is the load-bearing half: the producer's own frame-sample gate asserted presence
# and location but never type, and that is exactly how a `''` placeholder passed it three
# days before production began emitting `null`. A certificate repeating that omission would
# certify the same blind spot.
_ENVELOPE_FIELDS: Tuple[Tuple[str, tuple], ...] = (
    ('schema_version', (str,)),
    ('pipeline_id', (str,)),
    ('outcome_type', (str,)),
    ('seq', (int,)),
    ('stream_epoch', (int,)),
    ('trigger_reason', (str,)),
    ('prompt_version', (str,)),
    ('prompt_id', (str,)),
    ('prompt_hash', (str,)),
    ('data_origin', (str,)),
    ('config_fingerprint', (str,)),
    ('timestamp', (str,)),
    ('available_msc', (int,)),
    ('status', (str,)),
    ('result', (list,)),
    ('metadata', (dict,)),
)

# Per-symbol row fields, same rule. `evidence_as_of` and `breaking_episode_id` are
# nullable: outside an episode the id arrives as JSON null, and a row resting on no
# evidence carries no stamp — so None is a permitted value, while an empty string in the
# id's place is not (the producer fixed that on their side and named the rule explicitly).
_ROW_FIELDS: Tuple[Tuple[str, tuple], ...] = (
    ('symbol', (str,)),
    ('signal', (str,)),
    ('sentiment_score', (int, float)),
    ('confidence', (int, float)),
    ('reasoning', (str,)),
    ('urgency', (int, float)),
    ('is_breaking', (bool,)),
    ('basis', (str,)),
    ('evidence_as_of', (int, type(None))),
    ('breaking_episode_id', (str, type(None))),
    ('breaking_episode_start', (bool,)),
    ('sources', (list,)),
)

# Vocabularies we understand today. An unknown value is RECORDED, never refused — the
# reader must keep working when the producer adds one, which is the property asserted
# below by mutation rather than by assumption.
_KNOWN_SIGNALS = frozenset({'BUY', 'SELL', 'HOLD'})
_KNOWN_BASIS = frozenset({'llm', 'no_data', 'degraded'})
_KNOWN_STATUS = frozenset({'success', 'partial', 'error'})
_KNOWN_ORIGINS = frozenset({'live', 'synthetic'})

# A value no producer will ever emit, used to prove the reader tolerates the unknown.
_UNKNOWN_VOCABULARY_PROBE = 'finiex_unknown_vocabulary_probe'

# How far the producer's reported cadence may differ from the configured one before it is
# worth saying so. Both sides are operator-set integers, so this only absorbs float noise.
_CADENCE_TOLERANCE_S = 1.0

# The endpoint a release certificate may be signed against. A certificate against the
# development instance would pass and would certify nothing — it is the
# artifact-that-looks-like-proof the whole gate exists to prevent.
PRODUCTION_ENDPOINT_NAME = 'production'
PRODUCTION_ENVIRONMENT_NAME = 'production'

# Release version meaning nobody declared one, so the run is a rehearsal rather than a gate.
UNDECLARED_RELEASE = 'dev'


class SignalFeedContractValidator:
    """
    Turns read envelopes into contract verdicts, without touching the network.

    Stateless by construction: every method is a function of its arguments, so the same
    validator serves the release-gate run and the offline test over the frozen sample.
    """

    # ============================================
    # The envelope contract — transport-independent
    # ============================================

    def validate_wire_shape(self, envelope: Dict[str, Any]) -> List[FeedCheck]:
        """
        Assert one envelope's shape, stamps and vocabularies from the RAW payload.

        Takes the raw mapping and never the parsed model, which is what lets it run on an
        envelope our reader REFUSED. That case is not hypothetical: a field typed wrongly
        on our side makes the model raise, and a certificate that stopped there would say
        'the reader refused it' without naming the field that disagreed — which is exactly
        how the last such rejection got filed as the producer's outage.

        Args:
            envelope: The raw decoded payload

        Returns:
            One check per assertion, in reading order
        """
        checks: List[FeedCheck] = [
            self._check_schema_major(envelope),
            self._check_trigger_reason_location(envelope),
            self._check_collected_msc_absent(envelope),
        ]
        checks.extend(self._check_declared_fields(envelope))
        checks.append(self._check_evidence_matches_sources(envelope))
        checks.append(self._check_evidence_presence(envelope))
        checks.append(self._check_evidence_not_after_availability(envelope))
        checks.extend(self._check_episode_identity(envelope))
        return checks

    def validate_envelope(self, observation: FeedObservation) -> List[FeedCheck]:
        """
        The full envelope contract: the wire shape plus our reader's own guarantees.

        Args:
            observation: One read envelope, raw and parsed

        Returns:
            One check per assertion, in reading order
        """
        return (self.validate_wire_shape(observation.envelope)
                + self._check_reader_contract(observation.snapshot,
                                              observation.envelope))

    # ============================================
    # Series integrity — within one run
    # ============================================

    def validate_series(
        self,
        observations: List[FeedObservation],
        cadence_seconds_reported: Optional[float],
        cadence_minutes_configured: Optional[float],
    ) -> List[FeedCheck]:
        """
        Assert the stream position behaved across the run's observations.

        Args:
            observations: Envelopes read, in read order
            cadence_seconds_reported: Interval the producer reports for our source
            cadence_minutes_configured: Interval we have registered for it

        Returns:
            One check per assertion
        """
        sequences = [o.snapshot.seq for o in observations]
        epochs = {o.snapshot.stream_epoch for o in observations}

        backwards = [
            (sequences[i - 1], sequences[i])
            for i in range(1, len(sequences))
            if sequences[i] is not None and sequences[i - 1] is not None
            and sequences[i] < sequences[i - 1]
        ]
        # A single position cannot show that a series MOVED. The comparison loop below runs
        # zero times on one observation, so the check would pass while proving nothing —
        # the same shape as a loop over an empty set that once passed here for months. A
        # release gate that cannot evaluate an assertion must say so, not report success.
        comparable = len([s for s in sequences if s is not None]) >= 2
        checks = [
            FeedCheck(
                'seq_never_steps_backwards',
                comparable and not backwards,
                f'observed {sequences}' if comparable and not backwards
                else (f'seq stepped backwards: {backwards}' if backwards
                      else f'not evaluable — {len(sequences)} observation(s) carrying a '
                           f'position; two are the minimum that can show a series moved')),
            FeedCheck(
                'stream_epoch_stable_within_run',
                len(epochs) == 1,
                f'epoch {next(iter(epochs))}' if len(epochs) == 1
                else f'epoch changed within the run: {sorted(epochs)} — a cursor built '
                     f'against the previous one is meaningless in the new'),
        ]
        checks.append(self._check_cadence(
            cadence_seconds_reported, cadence_minutes_configured))
        return checks

    # ============================================
    # Provenance — which producer, and was it live
    # ============================================

    def validate_provenance(self, probe: FeedProbeResult) -> List[FeedCheck]:
        """
        Assert the run consumed the series a release is allowed to be certified against.

        Args:
            probe: What the run read, including the producer identity

        Returns:
            One check per assertion
        """
        identity = probe.identity
        journal = identity.journal_id if identity else None
        environment = identity.environment if identity else ''
        origins = {o.snapshot.data_origin for o in probe.observations}

        return [
            FeedCheck(
                'producer_named_a_journal',
                bool(journal),
                f'journal {journal}' if journal
                else 'the producer named no journal — a session against no identifiable '
                     'series is not a series anything can be certified against'),
            FeedCheck(
                'journal_is_not_a_development_instance',
                environment == PRODUCTION_ENVIRONMENT_NAME,
                f"the producer calls this journal '{environment}'"
                if environment == PRODUCTION_ENVIRONMENT_NAME
                else f"the producer calls this journal '{environment}', not "
                     f"'{PRODUCTION_ENVIRONMENT_NAME}' — a certificate signed here would "
                     f'pass and certify nothing'),
            FeedCheck(
                'endpoint_aimed_at_production',
                probe.endpoint_name == PRODUCTION_ENDPOINT_NAME,
                f"aimed at the '{probe.endpoint_name}' endpoint"),
            FeedCheck(
                'data_origin_is_live',
                origins == {'live'},
                f'origins observed: {sorted(origins)}'),
        ]

    # ============================================
    # Build provenance — whose code produced this
    # ============================================

    def validate_build(
        self,
        build: ProducerBuild,
        consumer_dirty: Optional[bool],
        consumer_uncommitted: int,
        release_version: str,
    ) -> List[FeedCheck]:
        """
        Assert both sides of the run are reproducible code.

        Two builds meet in one artifact: theirs, which produced the envelopes, and ours,
        which read them. A certificate naming neither cannot be re-derived by anybody —
        and the version string is not a substitute. Measured 2026-08-25: the producer
        deployed a new commit while `version` stayed '0.3.3', so two certificates taken
        twenty minutes apart came from different code and looked identical.

        Args:
            build: What /v1/build reported, possibly 'not offered'
            consumer_dirty: Whether OUR tree carried uncommitted changes, None when git
                could not be read
            consumer_uncommitted: How many files were uncommitted on our side
            release_version: Version being certified; the undeclared default marks a
                rehearsal

        Returns:
            One check per assertion
        """
        return [
            self._check_producer_build(build),
            self._check_consumer_build(
                consumer_dirty, consumer_uncommitted, release_version),
        ]

    # ============================================
    # Series integrity — across two CERTIFICATES
    # ============================================

    def validate_against_previous(
        self,
        journal_id: Optional[str],
        seq_last: Optional[int],
        previous: Optional[Dict[str, Any]],
        build: Optional[ProducerBuild] = None,
    ) -> List[FeedCheck]:
        """
        Compare this run against the last certificate signed on the same journal.

        The check only this artifact can make. A single session structurally cannot see a
        producer-side rewind: within one session nothing steps backwards, nothing arrives
        at all, and the staleness contract reports 'the producer went quiet' — the correct
        symptom with the wrong diagnosis. The certificate is the only thing that survives
        between runs, so this is where the comparison belongs.

        Comparison is bounded to ONE journal: a development certificate beside a
        production one would otherwise read as a rewind, because the two instances share
        a seq range.

        Args:
            journal_id: Journal this run read from
            seq_last: Highest seq this run observed
            previous: The most recent earlier certificate on the same journal, or None
            build: This run's producer build, so a restart between the two certificates
                can be named — a restart is exactly when a counter gets re-minted

        Returns:
            One check per assertion
        """
        if previous is None:
            detail = ('no earlier certificate on this journal — this run ESTABLISHES the '
                      'binding that later runs are checked against')
            return [
                FeedCheck('journal_matches_previous_certificate', True, detail),
                FeedCheck('seq_did_not_rewind_since_last_certificate', True, detail),
            ]

        previous_journal = (previous.get('producer') or {}).get('journal_id')
        previous_seq = (previous.get('series') or {}).get('seq_last')
        previous_stamp = previous.get('timestamp', 'unknown')

        rewound = (
            previous_seq is not None and seq_last is not None and seq_last < previous_seq)
        restart = self._describe_restart(previous, build)

        return [
            FeedCheck(
                'journal_matches_previous_certificate',
                journal_id == previous_journal,
                f'{journal_id} vs {previous_journal} in the certificate of '
                f'{previous_stamp}'),
            FeedCheck(
                'seq_did_not_rewind_since_last_certificate',
                not rewound,
                f'seq {previous_seq} → {seq_last} since {previous_stamp}{restart}'
                if not rewound
                else f'REWIND: seq {previous_seq} → {seq_last} since {previous_stamp}'
                     f'{restart}. Every new frame now sits below a held cursor and is '
                     f'ignored, while the connection stays perfectly healthy'),
        ]

    def _describe_restart(
        self, previous: Dict[str, Any], build: Optional[ProducerBuild]
    ) -> str:
        """
        Whether the producer restarted between two certificates, as a phrase to append.

        Worth naming on both outcomes. A restart is the moment a sequence counter can be
        re-minted, so a rewind seen across one is explained, and a clean sequence across one
        is evidence that their boot reconciliation recovered the counter — which is the
        thing this check exists to notice failing.

        Args:
            previous: The earlier certificate
            build: This run's producer build

        Returns:
            A phrase beginning with ', ' or the empty string when nothing is comparable
        """
        if build is None or not build.offered or build.started_at is None:
            return ''
        earlier = ((previous.get('build') or {}).get('producer') or {}).get('started_at')
        if not earlier:
            return ''
        if earlier == build.started_at.isoformat():
            return ', same producer process throughout'
        return (', across a producer RESTART — the one moment a sequence counter can be '
                're-minted')

    # ============================================
    # Recorded, not asserted
    # ============================================

    def collect_unread_fields(self, envelope: Dict[str, Any]) -> List[str]:
        """
        Fields on the wire that our reader does not consume.

        Recorded rather than asserted: the reader is deliberately tolerant of producer-side
        additions, so a grown envelope is news for the operator and not a failure. Same
        shape the live transport announces once per distinct set.

        Args:
            envelope: The raw decoded payload

        Returns:
            Sorted field names, row-level ones prefixed with 'result.'
        """
        unread = set(envelope) - set(SignalSnapshot.model_fields)
        for row in envelope.get('result') or []:
            unread |= {f'result.{key}'
                       for key in set(row) - set(SentimentResult.model_fields)}
        return sorted(unread)

    def collect_unknown_vocabulary(self, envelope: Dict[str, Any]) -> List[str]:
        """
        Closed-vocabulary values we do not know yet.

        Args:
            envelope: The raw decoded payload

        Returns:
            Sorted 'field=value' entries for every unrecognized value
        """
        unknown = set()
        if envelope.get('status') not in _KNOWN_STATUS:
            unknown.add(f"status={envelope.get('status')}")
        if envelope.get('data_origin') not in _KNOWN_ORIGINS:
            unknown.add(f"data_origin={envelope.get('data_origin')}")
        for row in envelope.get('result') or []:
            if row.get('signal') not in _KNOWN_SIGNALS:
                unknown.add(f"signal={row.get('signal')}")
            if row.get('basis') not in _KNOWN_BASIS:
                unknown.add(f"basis={row.get('basis')}")
        return sorted(unknown)

    def count_rows_without_evidence(self, envelope: Dict[str, Any]) -> int:
        """
        Rows resting on no evidence at all.

        Recorded so a certificate states when the evidence-absent branch was never
        exercised. An assertion whose loop runs zero times is one that cannot fail, and a
        green check proving nothing is worse than an absent one.

        Args:
            envelope: The raw decoded payload

        Returns:
            Number of rows carrying no evidence stamp
        """
        return sum(1 for row in envelope.get('result') or []
                   if row.get('evidence_as_of') is None)

    # ============================================
    # Internals — one assertion each
    # ============================================

    def _check_schema_major(self, envelope: Dict[str, Any]) -> FeedCheck:
        """
        The declared major is one this reader understands.

        The minor is recorded and NOT asserted: from their #65 note onward the producer
        bumps the minor for an additive field and the major for a breaking one, so a minor
        we have not seen means the shape grew — which is not a failure.

        Args:
            envelope: The raw decoded payload

        Returns:
            The check
        """
        version = str(envelope.get('schema_version', ''))
        major = schema_major(version)
        supported = ', '.join(f'{m}.x' for m in sorted(SUPPORTED_SCHEMA_MAJORS))
        return FeedCheck(
            'schema_major_supported',
            major in SUPPORTED_SCHEMA_MAJORS,
            f'schema {version} (supported majors: {supported})')

    def _check_trigger_reason_location(self, envelope: Dict[str, Any]) -> FeedCheck:
        """
        trigger_reason sits at the TOP LEVEL, not in metadata.

        The producer promoted it out of metadata and spent a schema major on the move. It
        is also the only way to tell a scheduled pass from an out-of-band one — timing
        cannot, because the envelope is stamped at the end of a variable-length run.

        Args:
            envelope: The raw decoded payload

        Returns:
            The check
        """
        top_level = envelope.get('trigger_reason')
        in_metadata = (envelope.get('metadata') or {}).get('trigger_reason')
        ok = isinstance(top_level, str) and bool(top_level)
        detail = f"trigger_reason='{top_level}' at the top level"
        if not ok and in_metadata:
            detail = (f"trigger_reason is only in metadata ('{in_metadata}') — the "
                      f'contract puts it at the top level from schema 2 on')
        elif not ok:
            detail = 'trigger_reason is absent at the top level'
        return FeedCheck('trigger_reason_at_top_level', ok, detail)

    def _check_collected_msc_absent(self, envelope: Dict[str, Any]) -> FeedCheck:
        """
        collected_msc is absent on the wire — the consumer stamps receipt.

        Args:
            envelope: The raw decoded payload

        Returns:
            The check
        """
        present = 'collected_msc' in envelope
        return FeedCheck(
            'collected_msc_absent_on_wire',
            not present,
            'absent, as contracted — the consumer stamps receipt' if not present
            else 'the producer sent collected_msc, which is OUR receive stamp: two '
                 'different instants would then share one field name')

    def _check_declared_fields(self, envelope: Dict[str, Any]) -> List[FeedCheck]:
        """
        Every contracted field is present, at its location, with its type.

        Args:
            envelope: The raw decoded payload

        Returns:
            One check per field, envelope-level first then row-level
        """
        checks = [
            self._check_field('envelope', envelope, name, types)
            for name, types in _ENVELOPE_FIELDS
        ]
        rows = envelope.get('result') or []
        for name, types in _ROW_FIELDS:
            checks.append(self._check_row_field(rows, name, types))
        return checks

    def _check_field(
        self, scope: str, mapping: Dict[str, Any], name: str, types: tuple
    ) -> FeedCheck:
        """
        One field's presence and type in one mapping.

        Args:
            scope: Label for the check name
            mapping: The mapping to read
            name: Field name
            types: Accepted types

        Returns:
            The check
        """
        check_name = f'{scope}_field_{name}'
        if name not in mapping:
            return FeedCheck(check_name, False, f'{name} is absent')
        value = mapping[name]
        if not self._is_typed(value, types):
            return FeedCheck(
                check_name, False,
                f'{name} is {type(value).__name__}, expected '
                f"{' | '.join(t.__name__ for t in types)}")
        return FeedCheck(check_name, True, f'{name}: {type(value).__name__}')

    def _check_row_field(
        self, rows: List[Dict[str, Any]], name: str, types: tuple
    ) -> FeedCheck:
        """
        One field across every per-symbol row, reporting the first row that disagrees.

        Aggregated per field rather than per row: a nine-symbol envelope would otherwise
        produce ninety checks saying the same thing, and the certificate needs the field's
        name, not one entry per symbol.

        Args:
            rows: The per-symbol rows
            name: Field name
            types: Accepted types

        Returns:
            The check
        """
        check_name = f'row_field_{name}'
        if not rows:
            return FeedCheck(check_name, False, 'the envelope carries no rows')
        for row in rows:
            outcome = self._check_field('row', row, name, types)
            if not outcome.ok:
                return FeedCheck(
                    check_name, False, f"{row.get('symbol', '?')}: {outcome.detail}")
        return FeedCheck(
            check_name, True,
            f'{name} present and typed across {len(rows)} rows')

    def _check_evidence_matches_sources(self, envelope: Dict[str, Any]) -> FeedCheck:
        """
        Each row's evidence_as_of equals the newest fetched_at it rests on.

        Compared at millisecond resolution, which is the producer's own: their stamp is
        millisecond-truncated while a source's fetched_at carries microseconds, so an exact
        comparison would fail on truncation alone.

        Args:
            envelope: The raw decoded payload

        Returns:
            The check
        """
        mismatches = []
        compared = 0
        for row in envelope.get('result') or []:
            newest = self._max_fetched_at(row)
            stamp = row.get('evidence_as_of')
            if newest is None or stamp is None:
                continue
            compared += 1
            if int(stamp) != int(newest.timestamp() * 1000):
                mismatches.append(
                    f"{row.get('symbol')}: {stamp} vs {int(newest.timestamp() * 1000)}")
        return FeedCheck(
            'evidence_matches_max_fetched_at',
            not mismatches,
            f'{compared} rows agree to the millisecond' if not mismatches
            else f'evidence_as_of disagrees with max(fetched_at): {mismatches}')

    def _check_evidence_presence(self, envelope: Dict[str, Any]) -> FeedCheck:
        """
        evidence_as_of is present exactly when the row rests on evidence.

        Args:
            envelope: The raw decoded payload

        Returns:
            The check
        """
        wrong = []
        for row in envelope.get('result') or []:
            has_evidence = self._max_fetched_at(row) is not None
            has_stamp = row.get('evidence_as_of') is not None
            if has_evidence != has_stamp:
                wrong.append(
                    f"{row.get('symbol')}: evidence={has_evidence} stamp={has_stamp}")
        return FeedCheck(
            'evidence_present_exactly_with_evidence',
            not wrong,
            'every row agrees' if not wrong else f'disagreeing rows: {wrong}')

    def _check_evidence_not_after_availability(
        self, envelope: Dict[str, Any]
    ) -> FeedCheck:
        """
        No evidence stamp lies after the envelope became available.

        Evidence the producer had not yet retrieved cannot have informed the pass, so a
        later stamp is a look-ahead on their side.

        Args:
            envelope: The raw decoded payload

        Returns:
            The check
        """
        available = envelope.get('available_msc')
        if not isinstance(available, int):
            return FeedCheck(
                'no_evidence_after_available_msc', False,
                'available_msc is absent, so nothing can be compared against it')
        late = [
            f"{row.get('symbol')}: {row.get('evidence_as_of')} > {available}"
            for row in envelope.get('result') or []
            if isinstance(row.get('evidence_as_of'), int)
            and row['evidence_as_of'] > available
        ]
        return FeedCheck(
            'no_evidence_after_available_msc',
            not late,
            f'newest evidence sits before availability ({available})' if not late
            else f'evidence stamped after the envelope was available: {late}')

    def _check_vocabularies_tolerated(self, envelope: Dict[str, Any]) -> FeedCheck:
        """
        An unknown closed-vocabulary value must never make the reader refuse an envelope.

        Proven by mutation rather than by assumption: the observed envelope is re-read with
        a value no producer will ever emit, and the reader must still parse it. Asserting
        this against only the values that happen to arrive would be an assertion that
        cannot fail.

        Args:
            envelope: The raw decoded payload

        Returns:
            The check
        """
        mutated = {
            **envelope,
            'status': _UNKNOWN_VOCABULARY_PROBE,
            'data_origin': _UNKNOWN_VOCABULARY_PROBE,
            'collected_msc': 0,
            'result': [
                {**row,
                 'signal': _UNKNOWN_VOCABULARY_PROBE,
                 'basis': _UNKNOWN_VOCABULARY_PROBE}
                for row in envelope.get('result') or []
            ],
        }
        try:
            SignalSnapshot.model_validate(mutated)
        except Exception as error:   # noqa: BLE001 — any refusal is the failure
            return FeedCheck(
                'closed_vocabulary_values_tolerated', False,
                f'the reader refused an unknown vocabulary value: '
                f'{type(error).__name__} — {error}')
        return FeedCheck(
            'closed_vocabulary_values_tolerated', True,
            'an unknown signal / basis / status / data_origin still parses')

    def _check_episode_identity(self, envelope: Dict[str, Any]) -> List[FeedCheck]:
        """
        The episode field group: an opaque id and a start FLAG.

        All three of our contract mismatches were in this group, which is why it gets its
        own assertions rather than resting on the type table above.

        Args:
            envelope: The raw decoded payload

        Returns:
            The checks
        """
        rows = envelope.get('result') or []
        populated = [row['breaking_episode_id'] for row in rows
                     if isinstance(row.get('breaking_episode_id'), str)
                     and row['breaking_episode_id']]
        splittable = [i for i in populated if len(i.split(':')) == 3]

        if populated:
            opacity = FeedCheck(
                'episode_id_is_opaque_when_populated',
                not splittable,
                f"{len(populated)} populated id(s), e.g. '{populated[0]}'"
                if not splittable
                else f'an id split cleanly into its three contracted segments '
                     f'({splittable}) — the contract calls it opaque, and code that '
                     f'starts splitting will break on the next query text')
        else:
            opacity = FeedCheck(
                'episode_id_is_opaque_when_populated', True,
                'not exercised: no row carried a populated id in this envelope')

        openers = [row for row in rows if row.get('breaking_episode_start') is True]
        without_id = [row.get('symbol') for row in openers
                      if not row.get('breaking_episode_id')]
        return [
            opacity,
            FeedCheck(
                'episode_start_implies_an_id',
                not without_id,
                f'{len(openers)} opener(s), each carrying an id' if not without_id
                else f'a pass opened an episode without naming it: {without_id}'),
        ]

    def _check_reader_contract(
        self, snapshot: SignalSnapshot, envelope: Dict[str, Any]
    ) -> List[FeedCheck]:
        """
        The production reader's own guarantees, exercised on this envelope.

        Uses the shipped SignalSnapshot and SignalDataProvider with no test-only shim —
        that the envelope goes through THOSE is the assertion.

        Args:
            snapshot: The parsed envelope
            envelope: The raw decoded payload

        Returns:
            The checks
        """
        available = envelope.get('available_msc')
        key = snapshot.get_resolution_key()
        expected_key = (
            datetime.fromtimestamp(available / 1000.0, tz=key.tzinfo)
            if isinstance(available, int) else None)

        order_key = snapshot.get_order_key()
        expected_order = (envelope.get('stream_epoch'), envelope.get('seq'), 0.0)

        symbol = snapshot.result[0].symbol if snapshot.result else ''
        provider = SignalDataProvider(
            SignalSeries(signal_kind='llm_sentiment', snapshots=[snapshot]))
        before = provider.nearest(key - timedelta(seconds=1), symbol)
        at = provider.nearest(key, symbol)

        return [
            FeedCheck(
                'envelope_parses_through_production_reader', True,
                'parsed through SignalSnapshot with no test-only shim'),
            self._check_vocabularies_tolerated(envelope),
            FeedCheck(
                'resolution_key_is_available_msc',
                expected_key is not None and key == expected_key,
                f'resolves at {key.isoformat()}'),
            FeedCheck(
                'order_key_is_well_formed',
                order_key == expected_order,
                f'(epoch, seq) = ({order_key[0]}, {order_key[1]})'),
            FeedCheck(
                'no_look_ahead_before_availability',
                before is None and at is not None,
                'nothing resolves one second before the availability stamp'
                if before is None and at is not None
                else f'a decision could see this envelope before it was available '
                     f'(before={before is not None}, at={at is not None})'),
        ]

    def _check_producer_build(self, build: ProducerBuild) -> FeedCheck:
        """
        The producer's build is identifiable and was built from committed code.

        'Not offered' passes and says so: the route is public by their default but sits
        behind a switch on their side, so its absence is a policy answer rather than a
        broken promise. Asserting against a promise nobody made is how a certificate
        starts reporting their configuration as our failure.

        Args:
            build: What /v1/build reported

        Returns:
            The check
        """
        if not build.offered:
            return FeedCheck(
                'producer_build_is_reproducible', True,
                f'not asserted: the producer does not publish its build ({build.detail})')
        if not build.commit:
            return FeedCheck(
                'producer_build_is_reproducible', False,
                'the producer published a build document without a commit, so the code '
                'behind this series cannot be named')
        if build.dirty:
            return FeedCheck(
                'producer_build_is_reproducible', False,
                f'the producer built {build.commit} from a tree with uncommitted changes, '
                f'so the code that produced these envelopes cannot be re-derived')
        return FeedCheck(
            'producer_build_is_reproducible', True,
            f'{build.version} @ {build.commit}, built clean')

    def _check_consumer_build(
        self,
        dirty: Optional[bool],
        uncommitted: int,
        release_version: str,
    ) -> FeedCheck:
        """
        OUR side was committed — asserted only when a release was actually declared.

        A rehearsal against a working tree is the normal case during development and must
        not go red. A run that NAMES a release version is different: the artifact then
        claims a version, and a claim whose code exists only in somebody's working tree
        cannot be re-derived by anyone, including us. Same intent-based split the config
        isolation guard uses.

        Args:
            dirty: Whether our tree carried uncommitted changes, None when git was unreadable
            uncommitted: How many files were uncommitted
            release_version: Version being certified

        Returns:
            The check
        """
        declared = release_version != UNDECLARED_RELEASE
        if dirty is None:
            return FeedCheck(
                'consumer_build_is_committed', not declared,
                'git was unreadable, so the code that produced this artifact cannot be named')
        if not dirty:
            return FeedCheck(
                'consumer_build_is_committed', True, 'our tree was clean')
        detail = f'{uncommitted} uncommitted file(s) in our tree'
        if not declared:
            return FeedCheck(
                'consumer_build_is_committed', True,
                f'{detail} — recorded, not asserted: no release version was declared, so '
                f'this run is a rehearsal')
        return FeedCheck(
            'consumer_build_is_committed', False,
            f'{detail}, so a certificate claiming {release_version} names a commit that '
            f'does not contain the code which produced it')

    def _check_cadence(
        self, reported: Optional[float], configured_minutes: Optional[float]
    ) -> FeedCheck:
        """
        The producer's own reported interval against the one we have registered.

        The producer's figure is authoritative and ours is a configuration, so a divergence
        means our staleness thresholds are calibrated against a cadence that no longer
        exists.

        Args:
            reported: Interval in seconds, as the producer reports it
            configured_minutes: Interval we have registered, in minutes

        Returns:
            The check
        """
        if reported is None or configured_minutes is None:
            return FeedCheck(
                'producer_cadence_matches_registered', False,
                f'not comparable: producer reported {reported}, '
                f'configured {configured_minutes}')
        configured = configured_minutes * 60.0
        ok = abs(reported - configured) <= _CADENCE_TOLERANCE_S
        return FeedCheck(
            'producer_cadence_matches_registered', ok,
            f'producer reports {reported:.0f}s, registered {configured:.0f}s')

    # ============================================
    # Small helpers
    # ============================================

    def _is_typed(self, value: Any, types: tuple) -> bool:
        """
        Type test that keeps bool and int apart.

        Python makes bool a subclass of int, so a plain isinstance would accept
        `is_breaking: 1` for a flag and `urgency: True` for a number. Both are exactly the
        shapes the producer's hardened gate now refuses, so ours must refuse them too.

        Args:
            value: The value to test
            types: Accepted types

        Returns:
            True when the value carries one of the accepted types
        """
        if isinstance(value, bool):
            return bool in types
        if bool in types:
            return False
        return isinstance(value, types)

    def _max_fetched_at(self, row: Dict[str, Any]) -> Optional[datetime]:
        """
        Newest retrieval stamp among a row's sources.

        Args:
            row: One per-symbol row

        Returns:
            The newest fetched_at, or None when the row rests on no evidence
        """
        stamps = []
        for source in row.get('sources') or []:
            raw = source.get('fetched_at')
            if not raw:
                continue
            stamps.append(datetime.fromisoformat(raw))
        if not stamps:
            return None
        return max(stamps)
