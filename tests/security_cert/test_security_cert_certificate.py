"""
FiniexTestingIDE - Security Study Certificate Validation (#358)

CI-friendly release-gate test: validates a committed security study certificate without running
the study. Mirrors test_field_study_certificate.py — SKIP (not FAIL) when no certificate exists,
otherwise assert it is not expired, shows PASSED, answers for every rule of the catalog, and is
complete.

Run the study and commit its certificate before a release:
    python python/cli/security_cert_cli.py run --release-version X.Y.Z
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pytest

from python.framework.reporting.certificates.certificate_index import CertificateIndex
from python.framework.store.store_registrations import CERTIFICATES_ROOT
from python.framework.types.acceptance_study_types.security_cert_types import SecurityRule


def _find_latest_report() -> Optional[Path]:
    """
    The newest certificate of this family, through the shared certificate index (#486).

    Returns:
        Path to the newest certificate, or None when none exists
    """
    return CertificateIndex(CERTIFICATES_ROOT).find_latest('security_cert')


def _latest_or_skip() -> dict:
    """The newest certificate's content, or a skip when there is none."""
    latest = _find_latest_report()
    if latest is None:
        pytest.skip('No certificate — see test_report_exists')
    return json.loads(latest.read_text(encoding='utf-8'))


class TestSecurityCertCertificate:
    """Release-gate validation of the committed security study certificate."""

    def test_report_exists(self):
        if _find_latest_report() is None:
            pytest.skip(
                'No security study certificate found.\n'
                'Run the study on the release commit:\n'
                '  python python/cli/security_cert_cli.py run --release-version X.Y.Z\n'
                'and commit the report under tests/security_cert/reports/.'
            )

    def test_report_not_expired(self):
        data = _latest_or_skip()
        valid_until_str = data.get('valid_until')
        assert valid_until_str, "Certificate missing 'valid_until'"
        valid_until = datetime.fromisoformat(valid_until_str)
        if valid_until.tzinfo is None:
            valid_until = valid_until.replace(tzinfo=timezone.utc)
        assert datetime.now(timezone.utc) <= valid_until, (
            f'Security study certificate EXPIRED ({valid_until_str}) — run the study again.'
        )

    def test_report_passed(self):
        data = _latest_or_skip()
        assert data.get('overall_status') == 'PASSED', (
            f"Security study certificate is FAILED — failed checks: {data.get('failed_checks')}"
        )

    def test_report_answers_for_every_rule(self):
        """A certificate taken before a rule was added does not answer for it."""
        data = _latest_or_skip()
        answered = {row['rule'] for row in data.get('rules', [])}
        assert answered == {rule.value for rule in SecurityRule}, (
            f'The certificate does not answer for: '
            f'{sorted({rule.value for rule in SecurityRule} - answered)}'
        )

    def test_report_integrity(self):
        data = _latest_or_skip()
        required = [
            'release_version', 'git_commit', 'timestamp', 'valid_until', 'overall_status',
            'stages', 'checks', 'rules', 'fees_charged', 'account_delta',
        ]
        missing = [f for f in required if f not in data]
        assert not missing, f'Certificate missing fields: {missing}'
