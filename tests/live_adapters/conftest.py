"""
FiniexTestingIDE - Live Adapter Tests Configuration

Provides --release-version CLI option and post-session report generation.
On success, writes a JSON receipt to tests/live_adapters/reports/ that documents
which adapter tests passed for a given release version.

Usage:
    pytest tests/live_adapters/ -v -m live_adapter --release-version 1.2.2
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import List

import pytest

from tests.shared.report_utils import get_git_commit


_REPORTS_DIR = Path('tests/live_adapters/reports')
_BROKER_SETTINGS_PATH = Path('configs/broker_settings/kraken_spot.json')
_REPORT_FIELDS_FROM_SETTINGS = ('api_base_url', 'dry_run', 'rate_limit_interval_s', 'request_timeout_s')


class _ResultCollector:
    """Tracks per-test outcomes and names during the session."""

    def __init__(self):
        self.passed: int = 0
        self.failed: int = 0
        self.skipped: int = 0
        self.tests_run: List[str] = []

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


def pytest_configure(config):
    config._live_adapter_results = _ResultCollector()
    config.pluginmanager.register(config._live_adapter_results)


def pytest_addoption(parser):
    """Add --release-version option for report generation."""
    parser.addoption(
        '--release-version',
        action='store',
        default='dev',
        help='Release version for live adapter report (e.g. 1.2.2). Defaults to "dev".',
    )


def pytest_sessionfinish(session, exitstatus):
    """Write release receipt after session completes."""
    results = session.config._live_adapter_results
    total = results.passed + results.failed + results.skipped

    # Skip report when no tests ran (e.g. collection errors, wrong directory)
    if total == 0:
        return

    # Skip report when all tests were skipped (no credentials — nothing to certify)
    if results.skipped == total:
        return

    release_version = session.config.getoption('release_version', default='dev')
    _write_report(release_version, results.passed, results.failed, results.skipped, results.tests_run)


def _write_report(
    release_version: str,
    passed: int,
    failed: int,
    skipped: int,
    tests_run: List[str],
) -> None:
    """
    Write a JSON release receipt for this live adapter test run.

    Args:
        release_version: Version string (e.g. '1.2.2' or 'dev')
        passed: Tests that passed
        failed: Tests that failed
        skipped: Tests that were skipped
        tests_run: Ordered list of test function names that were executed
    """
    timestamp = datetime.now(timezone.utc)
    timestamp_str = timestamp.strftime('%Y-%m-%dT%H:%M:%S+00:00')
    filename_ts = timestamp.strftime('%Y-%m-%d_%H%M%S')

    broker_settings_snapshot: dict = {}
    if _BROKER_SETTINGS_PATH.exists():
        try:
            with open(_BROKER_SETTINGS_PATH, 'r') as f:
                raw = json.load(f)
            broker_settings_snapshot = {k: raw[k] for k in _REPORT_FIELDS_FROM_SETTINGS if k in raw}
        except Exception:
            pass

    report = {
        'release_version': release_version,
        'git_commit': get_git_commit(),
        'timestamp': timestamp_str,
        'result': 'passed' if failed == 0 else 'failed',
        'tests_passed': passed,
        'tests_failed': failed,
        'tests_skipped': skipped,
        'tests_run': tests_run,
        'broker_settings': broker_settings_snapshot,
    }

    _REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    filename = f'live_adapter_report_{release_version}_{filename_ts}.json'
    report_path = _REPORTS_DIR / filename

    with open(report_path, 'w') as f:
        json.dump(report, f, indent=2)

    print(f'\nLive adapter report: {report_path}')
