"""
FiniexTestingIDE - Release Certificate Check (#640)

Whether the certificates a release rests on describe this release and this code.

Each family's own check judges its newest report alone: passed, not expired, complete. None asks
whether that report names this version or came from this code, so one taken weeks ago stays green,
and a run that certified nothing leaves the previous one standing. This asks it once, over all four,
before the tag.

It holds the CODE, not one commit: a report is committed after it is taken, so the tag can never be
the commit a certificate names. What has to hold is that only reports changed since.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from python.framework.reporting.certificates.certificate_index import (
    REPORTS_SUBDIR,
    CertificateIndex,
)
from python.framework.store.store_registrations import CERTIFICATES_ROOT
from python.framework.types.certificate_types import (
    CertificateStatus,
    ReleaseCertificateVerdict,
    ReleaseCheckResult,
)
from python.framework.utils.git_info_utils import (
    changed_paths_since,
    get_git_commit,
    get_git_info,
)
from python.framework.utils.time_utils import parse_datetime

# The families a release rests on, by the directory name the certificate index knows them under.
# The benchmark has a certificate too, but it describes the machine's throughput and is taken before
# the others, so it is no part of this question.
RELEASE_CERTIFICATE_FAMILIES = (
    'live_adapters', 'live_field_study', 'security_cert', 'live_signal_feed')

_LIVE_ADAPTER_FAMILY = 'live_adapters'

# How many changed paths a refusal names before it only counts them.
_PATHS_NAMED = 3


class ReleaseCertificateCheck:
    """
    Judges the newest certificate of every release family against one release.

    Args:
        root: The tests tree the certificate families live under
        now: The instant validity is compared against; current UTC when not given
    """

    def __init__(self, root: Path = CERTIFICATES_ROOT, now: Optional[datetime] = None):
        self._root = Path(root)
        self._now = now or datetime.now(timezone.utc)

    def run(self, release_version: str) -> ReleaseCheckResult:
        """
        Judge every family and the tree.

        Args:
            release_version: The release about to be tagged

        Returns:
            One verdict per family, the tree's state and the commit it stands on
        """
        index = CertificateIndex(self._root)
        return ReleaseCheckResult(
            release_version=release_version,
            head_commit=get_git_commit(),
            tree_problem=self._tree_problem(),
            verdicts=[self._judge(index, family, release_version)
                      for family in RELEASE_CERTIFICATE_FAMILIES],
        )

    @staticmethod
    def print_result(result: ReleaseCheckResult) -> None:
        """
        Print the verdicts, what is wrong with each, and whether the release may be tagged.

        Args:
            result: The check's result
        """
        print('=' * 76)
        print(f"  🔏 RELEASE CERTIFICATES — {result.release_version}"
              f" · tree on {result.head_commit or 'unknown'}")
        print('=' * 76)
        for verdict in result.verdicts:
            mark = '❌' if verdict.problems else '✅'
            status = verdict.status.value if verdict.status else '-'
            print(f"  {mark} {verdict.family:<18} {verdict.release_version or '-':<8}"
                  f" {verdict.git_commit or '-':<10} {status}")
            for problem in verdict.problems:
                print(f'       → {problem}')
        if result.tree_problem:
            print(f'  ❌ tree: {result.tree_problem}')
        print('-' * 76)
        if result.passed():
            print('  ✅ All four describe this release and this code. It may be tagged.')
        else:
            print('  ❌ REFUSED: do not tag this.')
        print('=' * 76)

    def _tree_problem(self) -> Optional[str]:
        """
        Why the tree itself cannot be tagged.

        Returns:
            The sentence, or None when the tree is clean
        """
        info = get_git_info()
        if info is None:
            return 'Its state could not be read: git did not answer.'
        if info.dirty:
            return (f'It holds {info.uncommitted_count} uncommitted change(s). A report in no '
                    'commit and code nobody certified look alike from here.')
        return None

    def _judge(self, index: CertificateIndex, family: str,
               release_version: str) -> ReleaseCertificateVerdict:
        """
        Judge one family's newest certificate.

        Args:
            index: The certificate index
            family: The family's directory name
            release_version: The release about to be tagged

        Returns:
            What it states and every reason it does not stand
        """
        newest = index.find_latest(family)
        if newest is None:
            return ReleaseCertificateVerdict(family=family, problems=['It has no certificate.'])
        try:
            data = json.loads(newest.read_text(encoding='utf-8'))
        except (OSError, json.JSONDecodeError) as error:
            return ReleaseCertificateVerdict(
                family=family, file=newest.name,
                problems=[f'Its certificate cannot be read ({error}).'])

        verdict = ReleaseCertificateVerdict(
            family=family,
            file=newest.name,
            release_version=data.get('release_version'),
            git_commit=data.get('git_commit'),
        )
        problems: List[str] = []
        if verdict.release_version != release_version:
            problems.append(f'It names {verdict.release_version}, not {release_version}.')
        try:
            verdict.status = CertificateStatus(data.get('overall_status'))
        except ValueError:
            problems.append(f"Its verdict is unknown: {data.get('overall_status')}.")
        if verdict.status is not None and verdict.status is not CertificateStatus.PASSED:
            problems.append(f'Its verdict is {verdict.status.value}.')
        if data.get('git_dirty') is not False:
            problems.append('It was not taken from a clean tree.')
        problems.extend(self._validity_problems(data))
        if family == _LIVE_ADAPTER_FAMILY:
            problems.extend(self._real_order_problems(data))
        problems.extend(self._code_problems(verdict))
        verdict.problems = problems
        return verdict

    def _validity_problems(self, data: Dict[str, Any]) -> List[str]:
        """
        Whether a certificate is still within its validity window.

        Args:
            data: The certificate

        Returns:
            The problem, or nothing
        """
        valid_until = data.get('valid_until')
        if not valid_until:
            return ['It states no validity.']
        if parse_datetime(valid_until) < self._now:
            return [f'It expired on {valid_until}.']
        return []

    def _real_order_problems(self, data: Dict[str, Any]) -> List[str]:
        """
        Whether a live adapter certificate saw a real order. A run without the flag writes one that
        reads PASSED and carries a warning, which is a legitimate rehearsal and no release proof.

        Args:
            data: The certificate

        Returns:
            The problem, or nothing
        """
        phases = (data.get('observed') or {}).get('phases') or []
        if any(phase.get('dry_run') is False for phase in phases):
            return []
        return ['No real-order phase ran: it proves the validate path only.']

    def _code_problems(self, verdict: ReleaseCertificateVerdict) -> List[str]:
        """
        Whether the code still is what the certificate was taken on, and note what changed.

        Args:
            verdict: The verdict being built, which receives the changed paths

        Returns:
            The problem, or nothing
        """
        if not verdict.git_commit:
            return ['It names no commit.']
        changed = changed_paths_since(verdict.git_commit)
        if changed is None:
            return [f"Its commit {verdict.git_commit} is not in this repository's history, as "
                    'after an amend, so nothing ties it to this code.']
        outside = [path for path in changed if not _is_a_report(path)]
        verdict.changed_outside_reports = outside
        if not outside:
            return []
        named = ', '.join(outside[:_PATHS_NAMED])
        return [f'{len(outside)} file(s) outside the reports changed since {verdict.git_commit}, '
                f'such as {named}.']


def _is_a_report(path: str) -> bool:
    """
    Whether a repository-relative path is a file in a certificate family's reports directory.

    Args:
        path: The path as git names it

    Returns:
        True for a file under the tests tree inside a reports directory
    """
    parts = Path(path).parts
    return parts[:1] == CERTIFICATES_ROOT.parts[:1] and REPORTS_SUBDIR in parts
