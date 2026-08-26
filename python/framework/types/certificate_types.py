"""
FiniexTestingIDE - Certificate Types
Shared identity for the release-gate certificates (benchmark, live adapters, field study,
signal feed).

Each of the four used to answer "what am I certifying, and from what code" for itself, and
each answered incompletely in a different way: only one recorded the version the TREE says,
only one recorded whether the tree was dirty, one had no validity window at all, and one
named its status field differently from the other three. This unit is the single answer.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional


class CertificateStatus(str, Enum):
    """
    A certificate's verdict — one vocabulary across all four producers.

    PASSED / FAILED are the only outcomes a release gate may report. A certificate that
    could not be taken is not a third state: it is simply absent, and the checklist item
    stays open.
    """
    PASSED = 'PASSED'
    FAILED = 'FAILED'


@dataclass
class WorkspaceOverrides:
    """
    Which personal config overrides existed while the certificate was taken.

    Names and a count only, never key paths or values: every certificate is committed to a
    public repository, so anything recorded here is published. A file whose name has no
    committed counterpart is counted rather than named, which makes the listing incapable
    of disclosing what the private workspace holds.
    """
    files_present: List[str] = field(default_factory=list)
    unnamed_files: int = 0
    applied: bool = False

    def to_dict(self) -> Dict[str, Any]:
        """
        Serializable form for the certificate body.

        Returns:
            The three published fields
        """
        return {
            'files_present': list(self.files_present),
            'unnamed_files': self.unnamed_files,
            'applied': self.applied,
        }


@dataclass
class CertificateIdentity:
    """
    What a certificate certifies, and the code and environment it was taken from.

    Two version fields, and the distinction is the point: `release_version` is what the
    operator DECLARED on the command line, `app_version` is what the tree SAYS. Recording
    only the declaration is how an artifact ends up naming a release it was not taken from.

    `isolation_active` and `workspace_overrides` describe the environment rather than the
    subject — every producer needs them, and they mean the same thing for all four. What
    was exercised (a scenario set, a profile, a broker) stays with the producer.
    """
    record_kind: str
    release_version: str
    app_version: str
    timestamp: datetime
    valid_until: datetime
    git_commit: str
    git_branch: Optional[str] = None
    git_dirty: bool = False
    uncommitted_count: int = 0
    comment: Optional[str] = None
    isolation_active: bool = False
    workspace_overrides: WorkspaceOverrides = field(default_factory=WorkspaceOverrides)

    def version_mismatch(self) -> Optional[str]:
        """
        Whether the declared release disagrees with the tree.

        'dev' declares nothing and is therefore exempt — it marks a rehearsal, and a
        rehearsal is allowed to run against any tree.

        Returns:
            A warning naming both versions, or None when they agree or nothing was declared
        """
        if self.release_version == 'dev' or self.release_version == self.app_version:
            return None
        return (f"VERSION MISMATCH: certifying '{self.release_version}' from a tree that "
                f"says '{self.app_version}' (configs/app_config.json). Bump the version "
                f'before taking the certificate, or the artifact names a release it did '
                f'not measure.')

    def dirty_tree_warning(self) -> Optional[str]:
        """
        Whether a declared release is being certified from uncommitted work.

        A certificate records a commit; on a dirty tree that commit does not contain the
        code that produced the artifact, so the reference is misleading rather than merely
        imprecise. A rehearsal ('dev') is exempt.

        Returns:
            A warning naming the uncommitted count, or None when the tree is clean
        """
        if self.release_version == 'dev' or not self.git_dirty:
            return None
        return (f'DIRTY TREE: certifying {self.release_version} from a working tree with '
                f'{self.uncommitted_count} uncommitted change(s). The recorded commit '
                f'{self.git_commit} does not contain the code that produced this artifact.')

    def to_dict(self) -> Dict[str, Any]:
        """
        Serializable form, spread into the certificate body.

        Returns:
            The identity fields, timestamps as ISO strings
        """
        return {
            'record_kind': self.record_kind,
            'release_version': self.release_version,
            'app_version': self.app_version,
            'timestamp': self.timestamp.isoformat(),
            'valid_until': self.valid_until.isoformat(),
            'git_commit': self.git_commit,
            'git_branch': self.git_branch,
            'git_dirty': self.git_dirty,
            'uncommitted_count': self.uncommitted_count,
            'comment': self.comment,
            'isolation_active': self.isolation_active,
            'workspace_overrides': self.workspace_overrides.to_dict(),
        }
