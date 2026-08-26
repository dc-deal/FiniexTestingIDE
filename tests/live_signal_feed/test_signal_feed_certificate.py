"""
FiniexTestingIDE - Signal Feed Certificate Validation (#466)

CI-friendly release-gate test: validates the COMMITTED certificate without touching the
producer. Mirrors test_field_study_certificate.py and test_benchmark_certificate.py — SKIP
(not FAIL) when no certificate exists, otherwise assert it is not expired, shows PASSED,
and is complete.

It also skips when the same session takes a live reading: that certificate is written at
session finish, so a read-back in the same session would describe the PREVIOUS run while
appearing to describe this one. Run it as its own command after the certification run.

Generate + commit a certificate before a release:
    FINIEX_CONFIG_ISOLATION=0 pytest tests/live_signal_feed/ -v -m live_signal_feed \
        --release-version X.Y.Z
"""

import json
from datetime import datetime, timezone

import pytest

from python.framework.reporting.certificates.signal_feed_certificate import SignalFeedCertificate
from tests.live_signal_feed.conftest import probe_planned


def _committed(request):
    """
    The newest committed certificate, or a skip when there is nothing to read back.

    Args:
        request: The pytest request, for the session state

    Returns:
        The parsed certificate
    """
    if probe_planned(request.config):
        pytest.skip(
            'this session takes a live reading; its certificate is written at session '
            'finish, so a read-back here would describe the previous run')
    latest = SignalFeedCertificate.find_latest(
        request.config.getoption('reports_dir'))
    if latest is None:
        pytest.skip(
            'No signal feed certificate found.\n'
            'Run the suite against the production producer:\n'
            '  FINIEX_CONFIG_ISOLATION=0 pytest tests/live_signal_feed/ -v '
            '-m live_signal_feed --release-version X.Y.Z\n'
            'and commit the report under tests/live_signal_feed/reports/.')
    return json.loads(latest.read_text(encoding='utf-8'))


class TestSignalFeedCertificate:
    """Release-gate validation of the committed signal feed certificate."""

    def test_the_certificate_exists(self, request):
        """A release needs one, and its absence must be visible rather than assumed."""
        _committed(request)

    def test_the_certificate_is_not_expired(self, request):
        """
        Ninety days, matching the benchmark and field-study backstop.

        The producer's contract moves; a certificate older than that describes a shape
        nobody has checked since.
        """
        data = _committed(request)
        raw = data.get('valid_until')
        assert raw, "Certificate missing 'valid_until'"
        valid_until = datetime.fromisoformat(raw)
        if valid_until.tzinfo is None:
            valid_until = valid_until.replace(tzinfo=timezone.utc)
        assert datetime.now(timezone.utc) <= valid_until, (
            f'Signal feed certificate EXPIRED ({raw}) — re-run it against the production '
            f'producer.')

    def test_the_certificate_passed(self, request):
        """PASS/FAIL, with the failing checks named in the message."""
        data = _committed(request)
        failed = [c['name'] for c in data.get('checks', []) if not c.get('ok')]
        assert data.get('overall_status') == 'PASSED', (
            f'Signal feed certificate is FAILED — failing checks: {failed}')

    def test_the_certificate_names_its_journal(self, request):
        """
        The one field whose absence makes the artifact meaningless.

        Two producer instances share a schema, a pipeline_id and a seq range, so a
        certificate that cannot name its journal is indistinguishable from one taken
        against a development instance.
        """
        data = _committed(request)
        producer = data.get('producer') or {}
        assert producer.get('journal_id'), (
            'the committed certificate names no journal_id, so it cannot be told apart '
            'from one signed against the development instance')

    def test_the_certificate_is_complete(self, request):
        """Every section a later release compares against is present."""
        data = _committed(request)
        required = [
            'release_version', 'app_version', 'git_commit', 'git_dirty', 'timestamp',
            'valid_until', 'isolation_active', 'workspace_overrides',
            'overall_status', 'producer', 'series', 'provenance', 'cost', 'checks',
        ]
        missing = [field for field in required if field not in data]
        assert not missing, f'Certificate missing fields: {missing}'
