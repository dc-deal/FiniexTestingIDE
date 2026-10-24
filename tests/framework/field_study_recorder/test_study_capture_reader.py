"""
How a release certificate finds its run and reads its capture (#621): by the run's identity, never
by the newest file, and a capture it cannot read refused by name.
"""

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from python.framework.acceptance_studies.security_cert_stages import (
    SECURITY_STAGES,
    stage_unit_name,
)
from python.framework.constants.run_layout_constants import (
    FIELD_STUDY_CAPTURE,
    FOOTPRINT_FILE,
    RUN_HEADER_ARTIFACT,
    RUN_TYPE_AUTOTRADER,
    RUN_TYPE_SIMULATION,
    SECURITY_CERT_CAPTURE,
)
from python.framework.exceptions.acceptance_study_errors import AcceptanceCaptureUnreadableError
from python.framework.reporting.certificates.field_study_certificate import FieldStudyCertificate
from python.framework.reporting.certificates.security_cert_certificate import (
    SecurityCertCertificate,
)
from python.framework.reporting.certificates.study_capture_reader import (
    find_study_runs,
    read_study_capture,
)
from python.framework.reporting.io.run_header_io import write_run_header
from python.framework.types.api.report_types import RunHeader
from python.framework.types.run_purpose_types import RunPurpose
from python.framework.types.store_member_types import RunEntryMemberId

_START = datetime(2026, 10, 23, 12, 0, tzinfo=timezone.utc)
_MOCK_STAGES = Path('configs/autotrader_profiles/mock/security_cert')


def _study_run(root: Path, run_name: str, minutes: int, capture: str = FIELD_STUDY_CAPTURE,
               purpose: RunPurpose = RunPurpose.CERTIFICATE,
               run_type: str = RUN_TYPE_AUTOTRADER) -> Path:
    """
    A run directory with a header and a capture.

    Args:
        root: The run root
        run_name: The profile it ran
        minutes: Its start, in minutes after a fixed instant
        capture: The capture file it holds
        purpose: What its header says it is for
        run_type: Its pipeline

    Returns:
        The capture's path
    """
    run_id = f'20261023_12{minutes:02d}00_{minutes:08x}'
    run_dir = root / run_name / run_id
    write_run_header(RunHeader(run_id=run_id, start_time=_START + timedelta(minutes=minutes),
                               run_type=run_type, run_name=run_name, run_purpose=purpose), run_dir)
    path = run_dir / capture
    path.write_text('{"schema_version": "2.0"}\n{"event_type": "phase_start"}\n', encoding='utf-8')
    return path


class TestTheRunIsFoundByItsIdentity:

    def test_the_newest_start_wins_not_the_newest_file(self, tmp_path):
        """A rehearsal that aborted after the real run is newer on disk, and is not the run."""
        real = _study_run(tmp_path, 'my_study', minutes=30)
        older = _study_run(tmp_path, 'my_study', minutes=10)
        os.utime(older, (older.stat().st_atime, real.stat().st_mtime + 3600))

        candidates = find_study_runs(tmp_path, RunEntryMemberId.FIELD_STUDY_CAPTURE)

        assert [candidate.capture_path for candidate in candidates] == [real, older]

    def test_only_autotrader_runs_of_the_asked_name_and_purpose_count(self, tmp_path):
        _study_run(tmp_path, 'my_study', minutes=1, purpose=RunPurpose.FIXTURE)
        _study_run(tmp_path, 'other_study', minutes=2)
        _study_run(tmp_path, 'my_study', minutes=3, run_type=RUN_TYPE_SIMULATION)
        wanted = _study_run(tmp_path, 'my_study', minutes=4)

        candidates = find_study_runs(tmp_path, RunEntryMemberId.FIELD_STUDY_CAPTURE, run_name='my_study',
                                     run_purpose=RunPurpose.CERTIFICATE.value)

        assert [candidate.capture_path for candidate in candidates] == [wanted]

    def test_a_candidate_says_whether_it_closed(self, tmp_path):
        capture = _study_run(tmp_path, 'my_study', minutes=1)
        assert find_study_runs(tmp_path, RunEntryMemberId.FIELD_STUDY_CAPTURE)[0].closed is False
        (capture.parent / FOOTPRINT_FILE).write_text('{}', encoding='utf-8')
        assert find_study_runs(tmp_path, RunEntryMemberId.FIELD_STUDY_CAPTURE)[0].closed is True

    def test_the_field_study_takes_certificate_runs_unless_a_profile_is_named(self, tmp_path):
        rehearsal = _study_run(tmp_path, 'my_mock_study', minutes=20, purpose=RunPurpose.FIXTURE)
        real = _study_run(tmp_path, 'my_study', minutes=10)

        assert FieldStudyCertificate.find_latest_jsonl(str(tmp_path)) == real
        assert FieldStudyCertificate.find_latest_jsonl(
            str(tmp_path), profile_name='my_mock_study') == rehearsal

    def test_a_security_stage_takes_the_purpose_its_own_profile_declares(self, tmp_path):
        """The mock rehearsal's stages are fixtures and must be found as the real study's are."""
        expected = {}
        for minutes, stage in enumerate(SECURITY_STAGES):
            unit = stage_unit_name(stage, _MOCK_STAGES)
            expected[stage.stage_id] = _study_run(
                tmp_path, unit, minutes=minutes, capture=SECURITY_CERT_CAPTURE,
                purpose=RunPurpose.FIXTURE)

        found = SecurityCertCertificate.find_latest_captures(str(tmp_path), _MOCK_STAGES)

        assert found == expected


class TestACaptureIsReadOrRefusedByName:

    def test_a_cut_last_line_is_left_out_and_said(self, tmp_path):
        path = tmp_path / FIELD_STUDY_CAPTURE
        path.write_text('{"h": 1}\n{"a": 1}\n{"b": ', encoding='utf-8')

        capture = read_study_capture(path)

        assert capture.events == [{'a': 1}]
        assert capture.cut_last_line is True

    @pytest.mark.parametrize('text', [
        '', '\n\n', 'not json\n{"a": 1}\n', '{"h": 1}\n{"a\n{"b": 1}\n'])
    def test_an_empty_or_broken_capture_is_refused(self, tmp_path, text):
        path = tmp_path / FIELD_STUDY_CAPTURE
        path.write_text(text, encoding='utf-8')

        with pytest.raises(AcceptanceCaptureUnreadableError):
            read_study_capture(path)

    def test_a_capture_outside_a_run_directory_is_not_checked(self, tmp_path):
        path = tmp_path / FIELD_STUDY_CAPTURE
        path.write_text('{"phases": []}\n', encoding='utf-8')

        analysis = FieldStudyCertificate.analyze(str(path))

        assert analysis['run_directory'] == {
            'checked': False, 'reason': 'the capture does not lie in a run directory'}


class TestTheRunDirectoryIsSaidNeverFailed:

    def test_a_run_without_a_footprint_is_said_with_both_of_its_causes(self, tmp_path):
        """A run older than footprints closed fine; the warning must not claim it stopped."""
        capture = _study_run(tmp_path, 'my_study', minutes=1)

        analysis = FieldStudyCertificate.analyze(str(capture))

        assert analysis['run_directory']['close_state'] == 'not_closed'
        assert [warning for warning in analysis['warnings'] if 'not closed' in warning] == [
            'The run is not closed (NO FOOTPRINT — not running: it stopped before its close, or '
            'it ran before runs wrote one): its record may be incomplete.']

    def test_a_header_that_cannot_be_read_is_said_and_the_certificate_still_written(
            self, tmp_path):
        capture = _study_run(tmp_path, 'my_study', minutes=1)
        (capture.parent / RUN_HEADER_ARTIFACT).write_text('{"run_id": ', encoding='utf-8')

        analysis = FieldStudyCertificate.analyze(str(capture))

        assert analysis['run_directory']['checked'] is False
        assert analysis['run_directory']['reason'].startswith("the run's header cannot be read")
