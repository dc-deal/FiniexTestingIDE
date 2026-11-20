"""
The divergence gate's types (#512) — whether a backtest may be compared with a live session at
all, and if not, why.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import List, Optional, Tuple

from python.framework.types.validation_types import Severity, ValidationFinding


class GateChangeCategory(StrEnum):
    """
    Why a file of the backtest's code path may differ from the session's release.

    RECORDING_ONLY: the backtest records what it did, and computes nothing differently for it
    INPUTS_OF_RECORD: the backtest reads its inputs from the session's own records instead of the
        archive's
    ALGO_VIEW: what a decision logic reads of its positions (#653)
    """
    RECORDING_ONLY = 'recording_only'
    INPUTS_OF_RECORD = 'inputs_of_record'
    ALGO_VIEW = 'algo_view'


class GateFileChange(StrEnum):
    """
    How one file of the code path differs between the session's commit and the backtest's.

    UNCHANGED: the same text
    DOCSTRINGS_ONLY: the same syntax tree once docstrings are removed — it computes the same
    CHANGED: a different syntax tree
    ENTERED_PATH: in the backtest's code path and not in the session's — a new file, or one the
        path did not import before
    LEFT_PATH: in the session's code path and not in the backtest's
    """
    UNCHANGED = 'unchanged'
    DOCSTRINGS_ONLY = 'docstrings_only'
    CHANGED = 'changed'
    ENTERED_PATH = 'entered_path'
    LEFT_PATH = 'left_path'

    def needs_declaration(self) -> bool:
        """
        Whether a file changed this way is refused unless its release declares it.

        Returns:
            True for every change that can alter what the path computes
        """
        return self in (GateFileChange.CHANGED, GateFileChange.ENTERED_PATH,
                        GateFileChange.LEFT_PATH)


@dataclass(frozen=True)
class GateFileDeclaration:
    """
    What the session's release permits for one file of the code path.

    Args:
        categories: Why it may differ — one or more, where several changes met in one file
        syntax_digest: The syntax tree it may have, without docstrings
    """
    categories: Tuple[GateChangeCategory, ...]
    syntax_digest: str


@dataclass(frozen=True)
class GateFileVerdict:
    """
    One file of either code path, judged.

    Args:
        path: Its repository-relative path
        change: How it differs between the two commits
        declaration: What the release declares for it; None where it declares nothing
        syntax_digest: Its syntax tree at the backtest's commit; None where the backtest's path
            does not hold it
    """
    path: str
    change: GateFileChange
    declaration: Optional[GateFileDeclaration]
    syntax_digest: Optional[str]

    def is_refused(self) -> bool:
        """
        Whether this file makes the backtest incomparable.

        A file that left the path computes nothing in the backtest, so its declaration pins no
        tree; every other declared file must have exactly the tree its declaration names.

        Returns:
            True for an undeclared change, and for a declared file whose tree moved on since
        """
        if not self.change.needs_declaration():
            return False
        if self.declaration is None:
            return True
        if self.change is GateFileChange.LEFT_PATH:
            return False
        return self.declaration.syntax_digest != self.syntax_digest


@dataclass(frozen=True)
class GateCodePath:
    """
    The backtest's code path held against the session's, file by file.

    Args:
        release: The session's release — whose declarations apply
        session_commit: The framework commit the session ran
        backtest_commit: The framework commit the backtest ran
        files: Every file of either closure, by path
    """
    release: str
    session_commit: str
    backtest_commit: str
    files: List[GateFileVerdict]

    def refused(self) -> List[GateFileVerdict]:
        """
        The files that make the backtest incomparable.

        Returns:
            Those files, by path
        """
        return [verdict for verdict in self.files if verdict.is_refused()]


@dataclass(frozen=True)
class DivergenceGateResult:
    """
    Whether a backtest may be compared with the sessions of a segment, and every reason it may not.

    Args:
        findings: One finding per refusal, and per advice that refuses nothing
        code_path: The code path judged file by file; None where it could not be judged — a side
            uncommitted, or a commit this repository does not hold
    """
    findings: List[ValidationFinding] = field(default_factory=list)
    code_path: Optional[GateCodePath] = None

    def is_comparable(self) -> bool:
        """
        Whether nothing refuses the comparison.

        Returns:
            True when no finding is an error
        """
        return not any(finding.severity is Severity.ERROR for finding in self.findings)
