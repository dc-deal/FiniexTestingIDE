"""
Store health checks — the two dated claims the store catalog flags.

A release certificate and a broker's declared fee structure are both claims whose validity decays.
Whether one is flagged is a verdict, so it lives in a validator and comes back as findings rather
than as printed lines — which is what lets a surface other than the console show it. Held here: the
boundary of the fee window, an absent freeze date, the scope each finding names, and that the ids
the checks emit are ones the check catalog declares.
"""

import json
from datetime import datetime, timezone

from python.framework.reporting.certificates.certificate_index import CertificateIndex
from python.framework.types.validation_types import Severity, ValidationDomain
from python.framework.validators.store_health_checks import (
    CERTIFICATE_EXPIRED_CHECK,
    FEE_FREEZE_WINDOW_DAYS,
    FEE_STRUCTURE_FROZEN_LONG_CHECK,
    check_expired_certificates,
    check_fee_freeze_ages,
    check_store_health,
)
from python.framework.validators.validation_check_catalog import VALIDATION_CHECKS_BY_ID


class TestExpiredCertificates:
    """A release gate whose newest certificate has expired."""

    def test_each_expired_gate_is_one_advisory_scoped_to_its_family(self):
        findings = check_expired_certificates([
            ('benchmark', '1.4.0', '2026-12-14T10:00:00+00:00'),
            ('live_adapters', 'dev', '2026-12-09T10:00:00+00:00')])

        assert [f.scope for f in findings] == ['benchmark', 'live_adapters']
        assert all(f.check == CERTIFICATE_EXPIRED_CHECK for f in findings)
        assert all(f.severity is Severity.WARNING for f in findings), 'an advisory, never a refusal'
        assert all(f.domain is ValidationDomain.RELEASE for f in findings)
        assert '1.4.0' in findings[0].message and '2026-12-14' in findings[0].message

    def test_no_expired_gate_is_no_finding(self):
        assert check_expired_certificates([]) == []

    def test_the_index_and_the_check_meet(self, tmp_path):
        """What the certificate index calls expired is what the check reports — one finding."""
        family = tmp_path / 'some_gate' / 'reports'
        family.mkdir(parents=True)
        (family / 'x_report_1.0.0_2020-01-01_000000.json').write_text(json.dumps({
            'release_version': '1.0.0', 'timestamp': '2020-01-01T00:00:00+00:00',
            'valid_until': '2020-04-01T00:00:00+00:00'}), encoding='utf-8')
        index = CertificateIndex(tmp_path)
        index.rebuild()

        findings = check_expired_certificates(index.expired_families())
        assert [(f.scope, f.check) for f in findings] == [('some_gate', CERTIFICATE_EXPIRED_CHECK)]


class TestFeeFreezeAges:
    """A declared fee structure frozen longer ago than the window."""

    def test_a_freeze_past_the_window_is_flagged_with_its_age_and_the_window(self):
        days = FEE_FREEZE_WINDOW_DAYS + 1
        findings = check_fee_freeze_ages({'kraken_spot': ('2026-05-01', days)})

        assert len(findings) == 1
        finding = findings[0]
        assert finding.scope == 'kraken_spot'
        assert finding.check == FEE_STRUCTURE_FROZEN_LONG_CHECK
        assert finding.domain is ValidationDomain.BROKER
        assert finding.severity is Severity.WARNING
        assert '2026-05-01' in finding.message and f'{days} days' in finding.message
        assert f'{FEE_FREEZE_WINDOW_DAYS}-day' in finding.message

    def test_a_freeze_exactly_at_the_window_is_not_flagged(self):
        """Past the window is flagged; reaching it is not."""
        assert check_fee_freeze_ages({'kraken_spot': ('2026-05-01', FEE_FREEZE_WINDOW_DAYS)}) == []

    def test_a_broker_without_a_freeze_date_is_not_flagged(self):
        """An absence is not an age — a seed that records no freeze is not an old one."""
        assert check_fee_freeze_ages({'mt5': None}) == []

    def test_the_window_is_a_parameter(self):
        findings = check_fee_freeze_ages({'kraken_spot': ('2026-05-01', 45)}, window_days=30)
        assert [f.scope for f in findings] == ['kraken_spot']
        assert '30-day' in findings[0].message


class TestOverTheInstallation:
    """Both checks over this tree's own certificates and broker configurations — read only."""

    def test_far_in_the_future_every_gate_has_expired_and_every_id_is_declared(self):
        """
        The tracked certificates all carry a validity date, so a moment far ahead expires every
        gate — which exercises the whole path from the stores to the findings.
        """
        findings = check_store_health(now=datetime(2100, 1, 1, tzinfo=timezone.utc))

        assert any(f.check == CERTIFICATE_EXPIRED_CHECK for f in findings)
        for finding in findings:
            assert finding.check in VALIDATION_CHECKS_BY_ID, finding.check
            assert finding.scope, 'every finding names the gate or the broker it concerns'
