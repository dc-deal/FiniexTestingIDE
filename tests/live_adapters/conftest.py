"""
FiniexTestingIDE - Live Adapter Tests Configuration

Provides the --release-version / --comment options and post-session certificate generation.
Writes a JSON certificate to tests/live_adapters/reports/ documenting which adapter tests
passed for a given release, validated afterwards by test_live_adapter_certificate.py.

The certificate records what the fixtures OBSERVED, never what a config file declares. The
distinction is not academic: this suite's two decisive tests set `dry_run = False` on their
own adapter and place real orders, while `configs/broker_settings/kraken_spot.json` says
`true` — a certificate that re-read the file understated exactly what it existed to prove.

Usage:
    pytest tests/live_adapters/ -v -m live_adapter --release-version 1.2.2
"""

import json
import re
from pathlib import Path
from typing import Any, Dict, List

from python.framework.reporting.certificates.certificate_identity_builder import (
    build_certificate_identity,
)
from python.framework.types.certificate_types import CertificateStatus

_REPORTS_DIR = Path('tests/live_adapters/reports')


class _ResultCollector:
    """
    Tracks per-test outcomes and what the adapter fixtures actually ran against.

    The observed settings are registered BY the fixtures (see record_observed_adapter) rather
    than read back from configuration at write time, because the fixtures are the only place
    that knows the values the adapter was really built with.
    """

    def __init__(self):
        self.passed: int = 0
        self.failed: int = 0
        self.skipped: int = 0
        self.tests_run: List[str] = []
        self.observed_phases: List[Dict[str, Any]] = []

    def pytest_runtest_logreport(self, report):
        if report.when != 'call':
            return
        test_name = report.nodeid.split('::')[-1]
        if test_name not in self.tests_run:
            self.tests_run.append(test_name)
        if report.passed:
            self.passed += 1
        elif report.failed:
            self.failed += 1
        elif report.skipped:
            self.skipped += 1

    def record_phase(self, phase: str, dry_run: bool, api_base_url: str) -> None:
        """
        Register one adapter configuration a fixture actually built.

        Args:
            phase: What this adapter is used for ('validate_only' / 'real_orders')
            dry_run: The value set on the adapter, NOT the value in the config file
            api_base_url: The endpoint the adapter talked to
        """
        if any(entry['phase'] == phase for entry in self.observed_phases):
            return
        self.observed_phases.append({
            'phase': phase,
            'dry_run': dry_run,
            'api_base_url': api_base_url,
        })


def record_observed_adapter(request, phase: str, dry_run: bool, api_base_url: str) -> None:
    """
    Report an adapter's effective settings from the fixture that built it.

    Args:
        request: The pytest request, carrying the session's collector
        phase: What this adapter is used for ('validate_only' / 'real_orders')
        dry_run: The value the fixture set on the adapter
        api_base_url: The endpoint the adapter talked to
    """
    collector = getattr(request.config, '_live_adapter_results', None)
    if collector is not None:
        collector.record_phase(phase=phase, dry_run=dry_run, api_base_url=api_base_url)


def pytest_configure(config):
    config._live_adapter_results = _ResultCollector()
    config.pluginmanager.register(config._live_adapter_results)


def pytest_addoption(parser):
    """Add the certificate options for report generation."""
    parser.addoption(
        '--release-version',
        action='store',
        default='dev',
        help='Release version for the live adapter certificate (e.g. 1.2.2). Defaults to "dev".',
    )
    parser.addoption(
        '--comment',
        action='store',
        default=None,
        help='Optional tester comment stored in the certificate.',
    )


def pytest_sessionfinish(session, exitstatus):
    """Write the release certificate after the session completes."""
    results = session.config._live_adapter_results
    total = results.passed + results.failed + results.skipped

    # Skip report when no tests ran (e.g. collection errors, wrong directory)
    if total == 0:
        return

    # Skip report when all tests were skipped (no credentials — nothing to certify)
    if results.skipped == total:
        return

    # Skip report when no adapter was built at all. A validation-only invocation runs tests
    # in this directory and would otherwise write a certificate for a session that never
    # contacted the broker — an artifact asserting something nobody measured.
    if not results.observed_phases:
        return

    _write_report(
        release_version=session.config.getoption('release_version', default='dev'),
        comment=session.config.getoption('comment', default=None),
        results=results,
    )


def _write_report(release_version: str, comment: str, results: _ResultCollector) -> None:
    """
    Write the JSON certificate for this live adapter test run.

    Args:
        release_version: Version string (e.g. '1.2.2' or 'dev')
        comment: Optional operator note
        results: The session's collected outcomes and observed adapter settings
    """
    identity = build_certificate_identity(
        release_version=release_version, comment=comment)

    warnings: List[str] = []
    status = CertificateStatus.PASSED if results.failed == 0 else CertificateStatus.FAILED
    for identity_warning in (identity.version_mismatch(), identity.dirty_tree_warning()):
        if identity_warning:
            status = CertificateStatus.FAILED
            warnings.append(identity_warning)

    # A certificate that recorded no real order proved only that the API accepts syntax.
    # Saying so out loud is cheaper than a reader assuming it either way.
    if not any(entry['dry_run'] is False for entry in results.observed_phases):
        warnings.append(
            'NO REAL ORDER: every adapter in this run was built with dry_run=True, so the '
            'certificate covers API acceptance only — not order execution.')

    report = {
        **identity.to_dict(),
        'overall_status': status.value,
        'tests_passed': results.passed,
        'tests_failed': results.failed,
        'tests_skipped': results.skipped,
        'tests_run': results.tests_run,
        # What the fixtures actually built, per phase. Registered by them at construction;
        # never re-read from configs/broker_settings/ at write time.
        'observed': {'phases': results.observed_phases},
        'warnings': warnings,
    }

    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    version_tag = re.sub(r'[^a-zA-Z0-9._-]', '_', release_version)
    filename = (f'live_adapter_report_{version_tag}_'
                f"{identity.timestamp.strftime('%Y-%m-%d_%H%M%S')}.json")
    report_path = _REPORTS_DIR / filename

    with open(report_path, 'w') as f:
        json.dump(report, f, indent=2)

    print(f'\nLive adapter certificate: {report_path}  [{status.value}]')
    for warning in warnings:
        print(f'  ⚠️  {warning}')
