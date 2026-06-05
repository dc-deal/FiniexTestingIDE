"""
FiniexTestingIDE - Field Study Certificate Validation (#332)

CI-friendly release-gate test: validates a committed Field Study certificate without
running the live study. Mirrors test_benchmark_certificate.py — SKIP (not FAIL) when no
certificate exists, otherwise assert it is not expired, shows PASSED, and is complete.

Generate + commit a certificate before a release:
    python python/cli/field_study_certificate_cli.py generate --latest --release-version X.Y.Z
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import pytest

REPORTS_DIR = Path(__file__).parent / 'reports'


def _find_latest_report() -> Optional[Path]:
    """Return the newest committed certificate by timestamp, or None."""
    if not REPORTS_DIR.exists():
        return None
    reports = list(REPORTS_DIR.glob('field_study_report_*.json'))
    if not reports:
        return None

    latest_report = None
    latest_ts = None
    for report_path in reports:
        try:
            data = json.loads(report_path.read_text(encoding='utf-8'))
            ts = data.get('timestamp')
            if ts:
                parsed = datetime.fromisoformat(ts)
                if latest_ts is None or parsed > latest_ts:
                    latest_ts = parsed
                    latest_report = report_path
        except (json.JSONDecodeError, KeyError, ValueError):
            continue
    return latest_report


class TestFieldStudyCertificate:
    """Release-gate validation of the committed Field Study certificate."""

    def test_report_exists(self):
        latest = _find_latest_report()
        if latest is None:
            pytest.skip(
                'No Field Study certificate found.\n'
                'Run a Field Study live, then:\n'
                '  python python/cli/field_study_certificate_cli.py generate --latest '
                '--release-version X.Y.Z\n'
                'and commit the report under tests/live_field_study/reports/.'
            )

    def test_report_not_expired(self):
        latest = _find_latest_report()
        if latest is None:
            pytest.skip('No certificate — see test_report_exists')
        data = json.loads(latest.read_text(encoding='utf-8'))
        valid_until_str = data.get('valid_until')
        assert valid_until_str, "Certificate missing 'valid_until'"
        valid_until = datetime.fromisoformat(valid_until_str)
        if valid_until.tzinfo is None:
            valid_until = valid_until.replace(tzinfo=timezone.utc)
        assert datetime.now(timezone.utc) <= valid_until, (
            f"Field Study certificate EXPIRED ({valid_until_str}) — re-run the Field Study."
        )

    def test_report_passed(self):
        latest = _find_latest_report()
        if latest is None:
            pytest.skip('No certificate — see test_report_exists')
        data = json.loads(latest.read_text(encoding='utf-8'))
        assert data.get('overall_status') == 'PASSED', (
            f"Field Study certificate is FAILED — "
            f"failed={data.get('failed_phases')} missing={data.get('missing_phases')} "
            f"flat_at_end={data.get('flat_at_session_end')}"
        )

    def test_report_integrity(self):
        latest = _find_latest_report()
        if latest is None:
            pytest.skip('No certificate — see test_report_exists')
        data = json.loads(latest.read_text(encoding='utf-8'))
        required = [
            'release_version', 'git_commit', 'timestamp', 'valid_until',
            'overall_status', 'phases', 'flat_at_session_end', 'realized_cost',
        ]
        missing = [f for f in required if f not in data]
        assert not missing, f"Certificate missing fields: {missing}"
