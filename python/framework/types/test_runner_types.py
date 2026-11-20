"""
FiniexTestingIDE - Test Runner Types
What one suite run produced, and how it ended.
"""

from dataclasses import dataclass
from enum import Enum
from typing import List, Optional


class SuiteOutcome(Enum):
    """
    How one suite's run ended.

    Running suites one at a time made a single question enough: was the exit code zero. Running
    several at once splits that question, because the ways a suite can fail to produce a verdict
    multiply and they ask for different things from whoever reads the summary. A suite whose
    tests failed names a defect to fix; one that was killed names a machine that ran out of
    something; one that never started names work nobody has done yet. Folding the three into
    "failed" would report the second and third as the first.
    """

    PASSED = 'passed'
    TESTS_FAILED = 'tests_failed'
    INTERRUPTED = 'interrupted'
    INTERNAL_ERROR = 'internal_error'
    USAGE_ERROR = 'usage_error'
    TIMED_OUT = 'timed_out'
    KILLED = 'killed'
    NOT_STARTED = 'not_started'
    UNKNOWN = 'unknown'

    @classmethod
    def from_exit_code(cls, exit_code: int) -> 'SuiteOutcome':
        """
        Classify a finished pytest process by the code it exited with.

        Args:
            exit_code: The process's return code; negative where a signal ended it

        Returns:
            The outcome that code stands for, UNKNOWN for a code pytest does not document
        """
        if exit_code < 0:
            return cls.KILLED
        # 5 is pytest's "no tests collected" — an empty suite directory, not a failure.
        return {
            0: cls.PASSED,
            1: cls.TESTS_FAILED,
            2: cls.INTERRUPTED,
            3: cls.INTERNAL_ERROR,
            4: cls.USAGE_ERROR,
            5: cls.PASSED,
        }.get(exit_code, cls.UNKNOWN)

    def reached_a_verdict(self) -> bool:
        """
        Whether the suite ran far enough for its pass and fail counts to mean anything.

        Returns:
            True where the counts describe the suite, False where they describe a fragment of it
        """
        return self in (SuiteOutcome.PASSED, SuiteOutcome.TESTS_FAILED)

    def is_failure(self) -> bool:
        """
        Whether this outcome should make the whole run fail.

        Returns:
            True for everything but a passed suite and one that was never started; a suite the
            run never reached is not a verdict about it
        """
        return self not in (SuiteOutcome.PASSED, SuiteOutcome.NOT_STARTED)


@dataclass
class SuiteResult:
    """Result of a single test suite execution."""
    name: str
    passed: int
    failed: int
    errors: int
    skipped: int
    exit_code: int
    duration: float
    failed_tests: List[str]
    outcome: SuiteOutcome = SuiteOutcome.UNKNOWN
    lane: Optional[int] = None
