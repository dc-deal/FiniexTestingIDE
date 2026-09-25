"""
FiniexTestingIDE - Certificate Identity Builder
Builds the shared identity every release-gate certificate carries.

Mirrors `run_provenance_builder.build_run_provenance()`: one function that reads the
version-control state, the declared version and the environment, so the four certificate
producers stop deriving it four times and disagreeing four ways.

The version-control state is the run header's code identity (#551), captured through the same
`capture_code_identity` a run uses: one answer to "is this code exactly one commit", shared with
the live real-money guard, and a patch in `run_patches/` wherever the answer is no.
"""

import platform
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from python.configuration.app_config_manager import AppConfigManager
from python.framework.types.certificate_types import CertificateIdentity, WorkspaceOverrides
from python.framework.utils.config_merge_utils import is_config_isolation_active
from python.framework.utils.run_origin_builder import capture_code_identity

# The shared validity backstop. One window for all four certificates: they answer questions
# about the same release, so letting them expire on different dates would mean a release is
# partly certified — a state with no useful reading.
VALIDITY_DAYS = 90


def build_certificate_identity(
    release_version: str = 'dev',
    comment: Optional[str] = None,
    record_kind: str = 'certificate',
    validity_days: int = VALIDITY_DAYS,
    now: Optional[datetime] = None,
    reports_dir: Optional[str] = None,
) -> CertificateIdentity:
    """
    Read the declared version, the tree and the environment into one identity.

    Args:
        release_version: The version the operator declared; 'dev' marks a rehearsal
        comment: Optional operator note stored with the certificate
        record_kind: What kind of record this is (certificates use the default)
        validity_days: Days until the certificate expires
        now: Capture moment; defaults to the current UTC time. Wall-clock is correct here —
            this measures when the artifact was produced, not a simulated event
        reports_dir: Where this run writes its certificate. Untracked files there do not
            count as a dirty tree and are not in the patch — otherwise a run is dirtied by the
            artifact of the previous one, and a repeated release attempt fails for a reason
            that has nothing to do with the code

    Returns:
        The identity, ready to spread into a certificate body
    """
    stamped = now or datetime.now(timezone.utc)
    code_identity = capture_code_identity([], ignore_untracked_under=reports_dir)
    framework = code_identity.framework
    override_names, unnamed_count = workspace_override_files()
    isolation_active = is_config_isolation_active()

    return CertificateIdentity(
        record_kind=record_kind,
        release_version=release_version,
        app_version=AppConfigManager().get_version(),
        timestamp=stamped,
        valid_until=stamped + timedelta(days=validity_days),
        git_commit=framework.commit or 'unknown',
        # Three parts only: a patch release is not a different interpreter for any purpose a
        # certificate serves, and the full string carries a build date that would make two
        # otherwise identical records differ.
        python_version=platform.python_version(),
        git_branch=framework.branch,
        # An unreadable tree counts as dirty — for a declared release it is refused, never
        # certified as the commit nobody could read.
        git_dirty=code_identity.is_dirty(),
        uncommitted_count=framework.uncommitted_count,
        comment=comment,
        isolation_active=isolation_active,
        workspace_overrides=WorkspaceOverrides(
            files_present=override_names,
            unnamed_files=unnamed_count,
            # Isolation makes the loaders skip the personal workspace, so the files may
            # exist and still not have reached the run. Present and applied are different
            # facts and the certificate states both.
            applied=(not isolation_active
                     and bool(override_names or unnamed_count))),
        code_identity=code_identity,
    )


def workspace_override_files() -> Tuple[List[str], int]:
    """
    Which content-merge override files exist in the private workspace.

    Names only, and only names that already exist in configs/. A certificate is committed to
    a public repository, so a file whose name has no committed counterpart is counted rather
    than named — that makes the listing structurally incapable of disclosing what the private
    workspace contains.

    Returns:
        Tuple of (override names mirroring a committed config, count of further files)
    """
    user_dir = Path('user_configs')
    if not user_dir.is_dir():
        return [], 0

    committed = {path.name for path in Path('configs').glob('*.json')}
    present = [path.name for path in user_dir.glob('*.json')]
    named = sorted(name for name in present if name in committed)
    return named, len(present) - len(named)
