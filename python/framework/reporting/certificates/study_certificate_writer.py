"""
FiniexTestingIDE - Study Certificate Writer (#332, #358)

Writes an acceptance study's certificate: the analysis a study made of its capture, under the
identity every release certificate carries (`release_version` / `git_commit` / `timestamp` /
`valid_until` / `overall_status`). What makes a certificate trustworthy is the same for every
study — it fails when it names the wrong release, comes from uncommitted work, or names a commit
its runs did not run on — so it is decided here once; what the study judged is its analyzer's.
"""

import json
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from python.framework.reporting.certificates.certificate_identity_builder import (
    build_certificate_identity,
)
from python.framework.types.acceptance_study_types.study_capture_types import StudyEvidenceRun
from python.framework.types.certificate_types import CertificateIdentity
from python.framework.utils.file_utils import create_directory


def write_study_certificate(
    analysis: Dict[str, Any],
    sources: Dict[str, Any],
    release_version: str,
    comment: str,
    reports_dir: str,
    report_prefix: str,
    evidence: List[StudyEvidenceRun],
) -> Tuple[Path, Dict[str, Any]]:
    """
    Put the release identity on an analysis and write it as the study's certificate.

    Args:
        analysis: What the study's analyzer judged — its `overall_status` and its `warnings`
            among it
        sources: Where the analysis was read from, recorded beside it (`source_jsonl`, …)
        release_version: Version this run certifies (or 'dev')
        comment: Optional free-text note
        reports_dir: Target directory for the certificate
        report_prefix: The file name's stem ('field_study_report') — `_report_` is what the
            certificate index finds a certificate by
        evidence: The runs the analysis rests on — the certificate's commit is held to theirs,
            and its validity counts from the first one's start

    Returns:
        The written file and the certificate it holds
    """
    started = [run.started for run in evidence if run.started is not None]
    identity = build_certificate_identity(
        release_version=release_version, comment=comment, reports_dir=reports_dir,
        valid_from=min(started) if started else None)
    warnings = [w for w in (identity.version_mismatch(),
                            identity.dirty_tree_warning(),
                            evidence_commit_warning(identity, evidence)) if w]
    certificate = {
        **identity.to_dict(),
        **sources,
        'evidence_runs': [{'run_id': run.run_id, 'commit': run.commit,
                           'started': run.started.isoformat() if run.started else None}
                          for run in evidence],
        **analysis,
    }
    # A real-money certificate that names the wrong release, comes from uncommitted work, or
    # names a commit its runs did not run on is worse than none: the run cost money and the
    # artifact cannot be tied to the code that earned it.
    if warnings:
        certificate['overall_status'] = 'FAILED'
        certificate['warnings'] = warnings + certificate.get('warnings', [])

    reports_path = Path(reports_dir)
    create_directory(reports_path, holds='the study certificates')
    version_tag = re.sub(r'[^a-zA-Z0-9._-]', '_', release_version)
    filename = (f'{report_prefix}_{version_tag}_'
                f"{identity.timestamp.strftime('%Y-%m-%d_%H%M%S')}.json")
    out_path = reports_path / filename
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(certificate, f, indent=2)
    return out_path, certificate


def evidence_commit_warning(identity: CertificateIdentity,
                            evidence: List[StudyEvidenceRun]) -> Optional[str]:
    """
    Whether a declared release is certified on a commit its runs did not run on — a clean tree
    on another commit names code that never traded. A rehearsal ('dev') is exempt.

    Args:
        identity: The certificate's identity, read from the tree it is generated on
        evidence: The runs the certificate rests on

    Returns:
        A warning naming each run and its commit, or None when every run ran on the
        certificate's commit
    """
    if identity.release_version == 'dev':
        return None
    if not evidence:
        return ('EVIDENCE COMMIT: no run the certificate rests on names its commit, so the '
                'certificate cannot be tied to the code that ran.')
    others = [f"{run.run_id} ran on {run.commit[:8] if run.commit else 'an unknown commit'}"
              for run in evidence if run.commit != identity.git_commit]
    if not others:
        return None
    return (f"EVIDENCE COMMIT: {'; '.join(others)} — the certificate is generated on "
            f'{identity.git_commit[:8]}. Generate it on the commit the study ran on, or run the '
            f'study again.')
