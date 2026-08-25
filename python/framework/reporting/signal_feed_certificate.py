"""
FiniexTestingIDE - Signal Feed Acceptance Certificate (#466)

The release gate for the one external input a strategy's edge is built on. Every other
external dependency in this project carries a certificate — throughput (benchmark), broker
execution (live adapters), real-money order lifecycle (field study). This is the signal
feed's, and it follows the same conventions: `release_version` / `git_commit` / `timestamp`
/ `valid_until` / `overall_status`, written to a `reports/` directory, validated by a
CI-friendly read-back test.

It certifies that the producer's envelopes are readable, correctly shaped and honestly
stamped, and it records WHICH producer journal answered. That last part is why the
certificate exists at all: two producer instances share a schema, a pipeline_id and a seq
range, so a certificate that cannot name its journal is indistinguishable from one taken
against a development instance — an artifact that looks like proof.

Stages, kept apart on purpose: the observer READS, the validator JUDGES, this unit RECORDS.
"""

import json
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from python.framework.signal_data.signal_feed_observer import (
    BUILD_ROUTE,
    HEALTH_ROUTE,
    LATEST_ROUTE_TEMPLATE,
)
from python.framework.types.signal_certificate_types import (
    FeedCheck,
    FeedProbeResult,
    SignalFeedAssessment,
)
from python.framework.utils.git_info_utils import get_git_commit, get_git_info
from python.framework.validators.signal_feed_contract_validator import (
    SignalFeedContractValidator,
)

# Default home for committed certificates — sibling of tests/live_adapters/reports,
# tests/live_field_study/reports and tests/simulation/benchmark/reports.
DEFAULT_REPORTS_DIR = 'tests/live_signal_feed/reports'
# Certificate validity window (days) — matches the benchmark and field-study backstop.
VALIDITY_DAYS = 90
# Artifact name prefix, so the read-back test and the rewind comparison find each other.
REPORT_PREFIX = 'signal_feed_report'
# The transport this certificate was taken over. The stream (#468) adds the second value
# and reuses every assertion above it.
TRANSPORT_POLL = 'poll'


class SignalFeedCertificate:
    """Assembles, writes and reads back the signal feed release-gate certificate."""

    @staticmethod
    def assess(
        probe: FeedProbeResult,
        cadence_minutes_configured: Optional[float],
        release_version: str = 'dev',
        observation_gap_s: float = 0.0,
        reports_dir: str = DEFAULT_REPORTS_DIR,
    ) -> SignalFeedAssessment:
        """
        Turn one run's reads into verdicts, including the comparison against history.

        Args:
            probe: What the observer read from the producer
            cadence_minutes_configured: What we have registered for this source
            release_version: Version being certified; the default marks a rehearsal, in
                which an uncommitted tree of ours is recorded rather than failed
            observation_gap_s: Pause the run left between consecutive reads
            reports_dir: Directory holding earlier certificates

        Returns:
            Every verdict and every recorded value
        """
        validator = SignalFeedContractValidator()
        assessment = SignalFeedAssessment(
            probe=probe,
            cadence_minutes_configured=cadence_minutes_configured,
            observation_gap_s=observation_gap_s)

        # Transport comes first: a read that never arrived cannot be judged for shape, and
        # the certificate must say WHICH of the two failed — the address or the credential.
        assessment.checks.extend(SignalFeedCertificate._transport_checks(probe))

        # A refused payload is still shape-checked, so the certificate names the field
        # that disagreed rather than stopping at 'our reader said no'.
        for envelope in probe.unparsed_envelopes:
            assessment.checks.extend(validator.validate_wire_shape(envelope))

        if not probe.is_readable():
            assessment.checks.append(FeedCheck(
                'producer_was_readable', False,
                'the run could not obtain an identity and an envelope, so the contract '
                'was never exercised — the failures above are the finding'))
            return assessment

        git = get_git_info()
        assessment.checks.extend(validator.validate_build(
            build=probe.build,
            consumer_dirty=git.dirty if git else None,
            consumer_uncommitted=git.uncommitted_count if git else 0,
            release_version=release_version))
        assessment.checks.extend(validator.validate_provenance(probe))
        for observation in probe.observations:
            assessment.checks.extend(validator.validate_envelope(observation))

        identity = probe.identity
        assessment.checks.extend(validator.validate_series(
            probe.observations,
            identity.cadence_seconds if identity else None,
            cadence_minutes_configured))

        previous = SignalFeedCertificate.find_previous(
            reports_dir, identity.journal_id if identity else None)
        assessment.previous_certificate = (
            str(previous[0]) if previous is not None else None)
        assessment.checks.extend(validator.validate_against_previous(
            identity.journal_id if identity else None,
            assessment.get_seq_span()[1],
            previous[1] if previous is not None else None,
            probe.build))

        newest = probe.observations[-1]
        assessment.unread_fields = validator.collect_unread_fields(newest.envelope)
        assessment.unknown_vocabulary = validator.collect_unknown_vocabulary(
            newest.envelope)
        assessment.rows_without_evidence = validator.count_rows_without_evidence(
            newest.envelope)
        return assessment

    @staticmethod
    def generate(
        assessment: SignalFeedAssessment,
        release_version: str = 'dev',
        comment: str = '',
        reports_dir: str = DEFAULT_REPORTS_DIR,
    ) -> Path:
        """
        Write a PASS/FAIL certificate for one assessed run.

        Args:
            assessment: The assessed run
            release_version: Version this run certifies (or 'dev')
            comment: Optional free-text note
            reports_dir: Target directory for the certificate

        Returns:
            Path to the written certificate
        """
        now = datetime.now(timezone.utc)
        certificate = {
            'record_kind': 'certificate',
            'release_version': release_version,
            'git_commit': get_git_commit() or 'unknown',
            'timestamp': now.isoformat(),
            'valid_until': (now + timedelta(days=VALIDITY_DAYS)).isoformat(),
            'comment': comment,
            'overall_status': 'PASSED' if assessment.is_passed() else 'FAILED',
            **SignalFeedCertificate._body(assessment),
        }

        reports_path = Path(reports_dir)
        reports_path.mkdir(parents=True, exist_ok=True)
        version_tag = re.sub(r'[^a-zA-Z0-9._-]', '_', release_version)
        filename = (f'{REPORT_PREFIX}_{version_tag}_'
                    f"{now.strftime('%Y-%m-%d_%H%M%S')}.json")
        out_path = reports_path / filename
        with open(out_path, 'w', encoding='utf-8') as handle:
            json.dump(certificate, handle, indent=2)

        SignalFeedCertificate._print_summary(out_path, certificate, assessment)
        return out_path

    @staticmethod
    def find_previous(
        reports_dir: str, journal_id: Optional[str]
    ) -> Optional[tuple]:
        """
        The most recent earlier certificate taken against the same journal.

        Bounded to one journal on purpose: two producer instances share a seq range, so a
        development certificate sitting beside a production one would otherwise read as a
        rewind.

        Args:
            reports_dir: Directory holding certificates
            journal_id: Journal to compare within; None matches nothing

        Returns:
            (path, parsed certificate) for the newest match, or None
        """
        if not journal_id:
            return None
        newest = None
        for path, data in SignalFeedCertificate._read_all(reports_dir):
            if (data.get('producer') or {}).get('journal_id') != journal_id:
                continue
            stamp = data.get('timestamp') or ''
            if newest is None or stamp > newest[2]:
                newest = (path, data, stamp)
        return (newest[0], newest[1]) if newest else None

    @staticmethod
    def find_latest(reports_dir: str = DEFAULT_REPORTS_DIR) -> Optional[Path]:
        """
        The newest committed certificate, whichever journal it names.

        Args:
            reports_dir: Directory holding certificates

        Returns:
            Path to the newest certificate, or None when there is none
        """
        newest = None
        for path, data in SignalFeedCertificate._read_all(reports_dir):
            stamp = data.get('timestamp') or ''
            if newest is None or stamp > newest[1]:
                newest = (path, stamp)
        return newest[0] if newest else None

    # ============================================
    # Internals
    # ============================================

    @staticmethod
    def _body(assessment: SignalFeedAssessment) -> Dict[str, Any]:
        """
        The certificate's recorded content, without the release metadata.

        Args:
            assessment: The assessed run

        Returns:
            The producer, series, provenance, cost and check sections
        """
        probe = assessment.probe
        identity = probe.identity
        newest = probe.observations[-1] if probe.observations else None
        envelope = newest.envelope if newest else {}
        seq_first, seq_last = assessment.get_seq_span()
        epochs = sorted({o.snapshot.stream_epoch for o in probe.observations
                         if o.snapshot.stream_epoch is not None})

        build = probe.build
        git = get_git_info()

        return {
            'build': {
                # Two builds meet in one artifact: theirs produced the envelopes, ours read
                # them. Naming neither makes the certificate unreproducible — and a version
                # string is no substitute, measured: the producer shipped a new commit while
                # `version` stayed '0.3.3'.
                'producer': {
                    'offered': build.offered,
                    'version': build.version,
                    'commit': build.commit,
                    'committed_at': _iso(build.committed_at),
                    'dirty': build.dirty,
                    'started_at': _iso(build.started_at),
                    'detail': build.detail,
                },
                'consumer': {
                    'branch': git.branch if git else '',
                    'commit': git.commit if git else '',
                    'committed_at': _iso(git.date) if git else None,
                    'message': git.message if git else '',
                    'dirty': git.dirty if git else None,
                    'uncommitted_count': git.uncommitted_count if git else 0,
                },
            },
            'producer': {
                'endpoint_aimed_at': probe.endpoint_name,
                'base_url': probe.base_url,
                'credential_source': probe.credential_source,
                'credential_configured': probe.credential_configured,
                'journal_id': identity.journal_id if identity else None,
                'journal_environment': identity.environment if identity else '',
                'engine_version': identity.engine_version if identity else '',
                'pass_timeout_s': identity.pass_timeout_s if identity else None,
                'budget_suspended': identity.budget_suspended if identity else False,
                'pipeline_id': probe.pipeline_id,
            },
            'series': {
                'seq_first': seq_first,
                'seq_last': seq_last,
                'stream_epochs': epochs,
                'observation_count': len(probe.observations),
                'observation_gap_s': assessment.observation_gap_s,
                'cadence_seconds_reported': (
                    identity.cadence_seconds if identity else None),
                'cadence_minutes_configured': assessment.cadence_minutes_configured,
                'previous_certificate': assessment.previous_certificate,
            },
            'provenance': {
                'schema_version': envelope.get('schema_version', ''),
                'data_origin': envelope.get('data_origin', ''),
                'trigger_reason': envelope.get('trigger_reason', ''),
                'prompt_version': envelope.get('prompt_version', ''),
                'prompt_id': envelope.get('prompt_id', ''),
                'prompt_hash': envelope.get('prompt_hash', ''),
                'config_fingerprint': envelope.get('config_fingerprint', ''),
                'row_count': len(envelope.get('result') or []),
                'frame_bytes': newest.frame_bytes if newest else 0,
                'envelope_age_at_fetch_s': (
                    round(newest.get_age_at_fetch_seconds(), 3) if newest else None),
                'unread_fields': assessment.unread_fields,
                'unknown_vocabulary': assessment.unknown_vocabulary,
                'rows_without_evidence': assessment.rows_without_evidence,
            },
            'cost': {
                'spent': 'nothing',
                'transport': TRANSPORT_POLL,
                'routes_used': [[call.method, call.path] for call in probe.routes_used],
            },
            'checks': [
                {'name': check.name, 'ok': check.ok, 'detail': check.detail}
                for check in assessment.checks
            ],
            'checks_passed': sum(1 for c in assessment.checks if c.ok),
            'checks_failed': len(assessment.get_failed()),
        }

    @staticmethod
    def _transport_checks(probe: FeedProbeResult) -> List[FeedCheck]:
        """
        The transport section: three named checks that exist whether they held or not.

        A check that only appears when it fails cannot be asserted on, and cannot be
        compared against the next certificate either. So each of the three is emitted
        every run, carrying either the observer's failure detail or what did answer.

        Args:
            probe: What the run read

        Returns:
            The transport checks, followed by any failure the three do not cover
        """
        failures = list(probe.transport_failures)

        def first(prefix: str) -> Optional[FeedCheck]:
            """The observer's failure under a name prefix, if it recorded one."""
            return next((f for f in failures if f.name.startswith(prefix)), None)

        health = first('health_route_answers')
        credential = first('credential_accepted')
        latest = first('latest_route_answers')
        covered = {check.name for check in (health, credential, latest) if check}

        return [
            health or FeedCheck(
                'health_route_answers', True,
                f'GET {HEALTH_ROUTE} answered without a token, so the address is right'),
            credential or FeedCheck(
                'credential_accepted', True,
                f'the producer accepted the token from {probe.credential_source}'),
            latest or FeedCheck(
                'latest_route_answers', bool(probe.observations),
                f'{len(probe.observations)} envelope(s) read'),
            FeedCheck(
                'run_touched_only_free_routes',
                SignalFeedCertificate._only_free_routes(probe),
                ' · '.join(call.describe() for call in probe.routes_used)
                or 'no route was called'),
        ] + [check for check in failures if check.name not in covered]

    @staticmethod
    def _only_free_routes(probe: FeedProbeResult) -> bool:
        """
        Whether the run called nothing but the two routes it is allowed to call.

        An allow-list, not a deny-list: refusing paths that end in '/run' would pass any
        other route somebody adds later, and the property being certified is that the run
        cannot have spent money — which only an exhaustive list can support.

        Args:
            probe: What the run read

        Returns:
            True when every recorded call was a GET on one of the two free routes
        """
        allowed = {
            HEALTH_ROUTE,
            BUILD_ROUTE,
            LATEST_ROUTE_TEMPLATE.format(pipeline_id=probe.pipeline_id),
        }
        return bool(probe.routes_used) and all(
            call.method == 'GET' and call.path in allowed
            for call in probe.routes_used)

    @staticmethod
    def _read_all(reports_dir: str) -> List[tuple]:
        """
        Every readable certificate in a directory.

        Args:
            reports_dir: Directory holding certificates

        Returns:
            (path, parsed certificate) pairs; unreadable files are skipped
        """
        directory = Path(reports_dir)
        if not directory.exists():
            return []
        found = []
        for path in sorted(directory.glob(f'{REPORT_PREFIX}_*.json')):
            try:
                found.append((path, json.loads(path.read_text(encoding='utf-8'))))
            except (json.JSONDecodeError, OSError):
                continue
        return found

    @staticmethod
    def _print_summary(
        out_path: Path, cert: Dict[str, Any], assessment: SignalFeedAssessment
    ) -> None:
        """
        Print a concise operator-facing certificate summary.

        Args:
            out_path: Where the certificate was written
            cert: The certificate content
            assessment: The assessed run, for the failure list
        """
        producer = cert['producer']
        series = cert['series']
        provenance = cert['provenance']
        mark = '✅' if cert['overall_status'] == 'PASSED' else '❌'
        journal = producer['journal_id'] or 'NONE'
        cadence = series['cadence_seconds_reported']
        configured = series['cadence_minutes_configured']

        print(f"\n{'=' * 68}")
        print(f"  {mark} SIGNAL FEED CERTIFICATE — {cert['overall_status']}")
        print(f"{'=' * 68}")
        print(f"  release: {cert['release_version']}  |  "
              f"commit: {cert['git_commit']}")
        print(f"  aimed at: {producer['endpoint_aimed_at']}  →  answered: {journal} "
              f"({producer['journal_environment']}) · engine "
              f"{producer['engine_version']}")
        print(f"  source:  {producer['pipeline_id']} · schema "
              f"{provenance['schema_version']} · origin {provenance['data_origin']} · "
              f"transport {cert['cost']['transport']}")
        print(f"  series:  seq {series['seq_first']} → {series['seq_last']} · epoch "
              f"{series['stream_epochs']} · cadence "
              f"{cadence if cadence is not None else '?'}s (registered "
              f"{configured * 60 if configured is not None else '?'}s)")
        producer_build = cert['build']['producer']
        consumer_build = cert['build']['consumer']
        print(f"  their build: {producer_build['version']} @ "
              f"{producer_build['commit'] or 'unpublished'}"
              f"{' (DIRTY)' if producer_build['dirty'] else ''}"
              f"  ·  started {producer_build['started_at'] or '?'}")
        print(f"  our build:   {consumer_build['branch']} @ {consumer_build['commit']}"
              f"{f" (DIRTY, {consumer_build['uncommitted_count']} files)" if consumer_build['dirty'] else ''}")
        print(f"  prompt:  v{provenance['prompt_version']} / "
              f"{provenance['prompt_hash']}  |  config fingerprint: "
              f"{provenance['config_fingerprint']}")
        print(f"  envelope age at fetch: {provenance['envelope_age_at_fetch_s']}s  |  "
              f"frame: {provenance['frame_bytes'] / 1024:.1f} KB  |  "
              f"{provenance['row_count']} rows")
        print(f"  checks:  {cert['checks_passed']} passed · "
              f"{cert['checks_failed']} failed        this run spent NOTHING "
              f"({len(cert['cost']['routes_used'])} GETs)")
        print(f"  recorded, not asserted: {len(provenance['unread_fields'])} unread "
              f"fields · rows_without_evidence: "
              f"{provenance['rows_without_evidence']}")
        for check in assessment.get_failed():
            print(f'  ❌ {check.name}: {check.detail}')
        print(f'  certificate: {out_path}')
        print(f"{'=' * 68}\n")


def _iso(value: Optional[datetime]) -> Optional[str]:
    """
    Serialize one instant for the artifact.

    Args:
        value: A tz-aware instant, or None

    Returns:
        Its ISO 8601 form, or None
    """
    return value.isoformat() if value is not None else None
