"""
FiniexTestingIDE - Study Certificate Writer (#332, #358)

Writes an acceptance study's certificate: the analysis a study made of its capture, under the
identity every release certificate carries (`release_version` / `git_commit` / `timestamp` /
`valid_until` / `overall_status`). What makes a certificate trustworthy is the same for every
study — it fails when it names the wrong release or comes from uncommitted work — so it is
decided here once; what the study judged is its analyzer's.
"""

import json
import re
from pathlib import Path
from typing import Any, Dict, Tuple

from python.framework.reporting.certificates.certificate_identity_builder import (
    build_certificate_identity,
)


def write_study_certificate(
    analysis: Dict[str, Any],
    sources: Dict[str, Any],
    release_version: str,
    comment: str,
    reports_dir: str,
    report_prefix: str,
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

    Returns:
        The written file and the certificate it holds
    """
    identity = build_certificate_identity(
        release_version=release_version, comment=comment, reports_dir=reports_dir)
    warnings = [w for w in (identity.version_mismatch(),
                            identity.dirty_tree_warning()) if w]
    certificate = {
        **identity.to_dict(),
        **sources,
        **analysis,
    }
    # A real-money certificate that names the wrong release, or comes from uncommitted
    # work, is worse than none: the run cost money and the artifact cannot be tied to
    # the code that earned it.
    if warnings:
        certificate['overall_status'] = 'FAILED'
        certificate['warnings'] = warnings + certificate.get('warnings', [])

    reports_path = Path(reports_dir)
    reports_path.mkdir(parents=True, exist_ok=True)
    version_tag = re.sub(r'[^a-zA-Z0-9._-]', '_', release_version)
    filename = (f'{report_prefix}_{version_tag}_'
                f"{identity.timestamp.strftime('%Y-%m-%d_%H%M%S')}.json")
    out_path = reports_path / filename
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(certificate, f, indent=2)
    return out_path, certificate
