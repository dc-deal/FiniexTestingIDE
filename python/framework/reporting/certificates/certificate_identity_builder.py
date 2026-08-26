"""
FiniexTestingIDE - Certificate Identity Builder
Builds the shared identity every release-gate certificate carries.

Mirrors `run_provenance_builder.build_run_provenance()`: one function that reads the
version-control state, the declared version and the environment, so the four certificate
producers stop deriving it four times and disagreeing four ways.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from python.configuration.app_config_manager import AppConfigManager
from python.framework.types.certificate_types import CertificateIdentity, WorkspaceOverrides
from python.framework.utils.config_merge_utils import is_config_isolation_active
from python.framework.utils.git_info_utils import get_git_info

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

    Returns:
        The identity, ready to spread into a certificate body
    """
    stamped = now or datetime.now(timezone.utc)
    git = get_git_info()
    override_names, unnamed_count = workspace_override_files()
    isolation_active = is_config_isolation_active()

    return CertificateIdentity(
        record_kind=record_kind,
        release_version=release_version,
        app_version=AppConfigManager().get_version(),
        timestamp=stamped,
        valid_until=stamped + timedelta(days=validity_days),
        git_commit=git.commit if git else 'unknown',
        git_branch=git.branch if git else None,
        git_dirty=git.dirty if git else False,
        uncommitted_count=git.uncommitted_count if git else 0,
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
