"""
Live adapter certificate validation — CI-side, no broker contact.

The suite had no such test: the certificate was written, committed, and never read again.
A stale artifact from an earlier release therefore satisfied nobody's assertion, which is
the same gap the benchmark and signal-feed certificates already close for themselves.

Reads committed artifacts only. Deliberately NOT marked `live_adapter`, so it runs in the
daily suite while the order-placing tests stay opt-in.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import pytest

REPORTS_DIR = Path(__file__).parent / 'reports'
CONFIGS_DIR = Path(__file__).resolve().parents[2] / 'configs'


def _find_latest_report() -> Optional[Path]:
    """
    The most recent certificate by filename timestamp.

    Returns:
        Path to the latest certificate, or None when none exists
    """
    reports = sorted(REPORTS_DIR.glob('live_adapter_report_*.json'))
    return reports[-1] if reports else None


def _load(path: Path) -> Dict[str, Any]:
    """
    Parse one certificate.

    Args:
        path: Certificate path

    Returns:
        The parsed body
    """
    return json.loads(path.read_text(encoding='utf-8'))


@pytest.fixture(scope='module')
def certificate() -> Dict[str, Any]:
    """
    The latest committed certificate.

    Returns:
        The parsed body; skips the module when the suite has never been run
    """
    latest = _find_latest_report()
    if latest is None:
        pytest.skip(
            'No live adapter certificate found. Run: '
            'pytest tests/live_adapters/ -v -m live_adapter --release-version X.Y.Z')
    return _load(latest)


class TestLiveAdapterCertificate:
    """Existence, expiry, verdict and structural completeness."""

    def test_certificate_passed(self, certificate):
        assert certificate['overall_status'] == 'PASSED', (
            f"Certificate status is {certificate['overall_status']}: "
            f"{certificate.get('warnings')}")
        assert certificate['tests_failed'] == 0

    def test_certificate_not_expired(self, certificate):
        """
        A validity window the artifact never had before.

        Without it a certificate from an earlier release stays green forever, which makes
        the release-gate checkbox meaningless.
        """
        valid_until = datetime.fromisoformat(certificate['valid_until'])
        assert valid_until > datetime.now(timezone.utc), (
            f"Certificate expired on {certificate['valid_until']} — re-run the suite")

    def test_identity_is_complete(self, certificate):
        """The shared identity fields every release-gate certificate carries."""
        missing = [f for f in (
            'record_kind', 'release_version', 'app_version', 'timestamp', 'valid_until',
            'git_commit', 'git_dirty', 'isolation_active', 'workspace_overrides',
            'overall_status', 'observed', 'warnings',
        ) if f not in certificate]
        assert not missing, f'Certificate missing identity fields: {missing}'

    def test_declared_release_matches_the_tree(self, certificate):
        """
        A declared release must agree with the version the tree carried.

        'dev' declares nothing and is exempt — it marks a rehearsal.
        """
        declared = certificate['release_version']
        if declared == 'dev':
            pytest.skip('Rehearsal certificate (dev) declares no release')
        assert declared == certificate['app_version'], (
            f"Certificate declares {declared} but was taken from a tree saying "
            f"{certificate['app_version']}")


class TestObservedSettings:
    """
    The certificate must state what the fixtures RAN, not what a config file declares.

    This is the defect the class exists for: the two decisive tests build their adapter with
    `dry_run = False` and place real orders, while `configs/broker_settings/kraken_spot.json`
    says `true`. The old certificate re-read that file and therefore understated the one
    thing it was taken to prove.
    """

    def test_observed_phases_are_recorded(self, certificate):
        phases = certificate['observed']['phases']
        assert phases, 'No adapter phase was recorded — the certificate proves nothing'
        for entry in phases:
            assert set(entry) == {'phase', 'dry_run', 'api_base_url'}, (
                f'Unexpected observed shape: {sorted(entry)}')

    def test_a_real_order_phase_was_exercised(self, certificate):
        """
        At least one adapter ran with dry_run=False.

        A run of validate-only phases is a legitimate outcome, but then the certificate must
        carry the NO REAL ORDER warning rather than reading as a full proof.
        """
        phases = certificate['observed']['phases']
        real = [entry for entry in phases if entry['dry_run'] is False]
        if not real:
            assert any('NO REAL ORDER' in w for w in certificate['warnings']), (
                'No real-order phase ran and the certificate does not say so')
            pytest.skip('Validate-only certificate — correctly flagged')
        assert all(entry['api_base_url'].startswith('https://') for entry in real), (
            'A real-order phase must run against an https endpoint')


class TestWorkspacePrivacy:
    """
    The published listing of private overrides carries names and a count — never values.

    Certificates are committed to a public repository, so a well-meant extension that added
    the overridden keys or their values would publish the private workspace with every
    release. Pinned as a shape assertion for exactly that reason.
    """

    def test_workspace_overrides_carry_names_only(self, certificate):
        overrides = certificate['workspace_overrides']
        assert set(overrides) == {'files_present', 'unnamed_files', 'applied'}, (
            f'workspace_overrides carries unexpected keys: {sorted(overrides)}')

        committed = {path.name for path in CONFIGS_DIR.glob('*.json')}
        leaked = [name for name in overrides['files_present'] if name not in committed]
        assert not leaked, (
            f'Certificate names workspace files with no committed counterpart: {leaked}')
