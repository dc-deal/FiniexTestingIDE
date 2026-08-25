"""
FiniexTestingIDE - Run Outcome Types
Outcome classification shared by both pipelines (#372, §35).

One finished run answers one question: did it complete cleanly, did it complete but log
errors, did units fail, or did the process never complete at all. Each pipeline classifies
from its own result object (BatchExecutionSummary / AutoTraderResult); the CLIs only map the
outcome to a process exit code and never derive it themselves.
"""

from enum import Enum


class RunOutcome(Enum):
    """
    Outcome of one run, shared by the simulation batch and the AutoTrader session.

    SUCCESS: every unit completed, nothing logged an error
    FINISHED_WITH_ERRORS: the run completed, but errors were logged (the §35 error pot)
    FAILED: units failed (sim) or the session ended in an emergency shutdown (live)
    CRASHED: the process did not complete — an uncaught exception, or no result at all
    """
    SUCCESS = 'success'
    FINISHED_WITH_ERRORS = 'finished_with_errors'
    FAILED = 'failed'
    CRASHED = 'crashed'

    def get_exit_code(self) -> int:
        """
        Process exit code for this outcome.

        Returns:
            The exit code a supervisor reads: 0 clean, 1 the process did not complete,
            2 the run finished with failures, 3 the run finished but logged errors
        """
        return _EXIT_CODES[self]


# Kept beside the enum rather than inside it: an Enum member value must not be a mapping.
_EXIT_CODES = {
    RunOutcome.SUCCESS: 0,
    RunOutcome.CRASHED: 1,
    RunOutcome.FAILED: 2,
    RunOutcome.FINISHED_WITH_ERRORS: 3,
}
