"""
FiniexTestingIDE - Scenario Execution Errors
Exception types for scenario execution failures
"""


from typing import List
from python.framework.types.process_data_types import ProcessResult


class ScenarioPreparationError(Exception):
    """
    Raised when scenario preparation (warmup phase) fails.

    This includes failures in:
    - Data loading
    - Worker creation
    - Trade simulator setup
    - Bar rendering preparation

    Scenarios that fail preparation are excluded from execution.
    """
    pass


class ScenarioExecutionError(Exception):
    """
    Raised when scenario tick loop execution fails.

    This includes failures during:
    - Tick processing
    - Signal generation
    - Order execution
    - Statistics collection

    Execution errors are logged but do not stop other scenarios.
    """
    pass


class ScenarioStateError(Exception):
    """
    Raised when execute_tick_loop() is called without prior prepare_scenario().

    ScenarioExecutor requires two-phase execution:
    1. prepare_scenario() - warmup and setup
    2. execute_tick_loop() - actual tick processing

    Calling execute_tick_loop() without preparation is a programming error.
    """
    pass


class ScenarioExecutionError(Exception):
    """Base exception for scenario execution errors."""
    pass


class ScenarioPreparationError(ScenarioExecutionError):
    """Raised when scenario preparation fails."""
    pass


class WarmupBarValidationError(ScenarioExecutionError):
    """
    Raised when warmup bar validation fails.

    Fast performance check - counts only, no temporal validation.
    Used in BarRenderingController.inject_warmup_bars().
    """
    pass


class BatchExecutionError(ScenarioExecutionError):
    """
    Raised when one or more scenarios fail during batch execution.

    Contains details of all failed scenarios for comprehensive error reporting.
    Allows batch to continue executing remaining scenarios.

    WORKFLOW:
    1. Batch executes all scenarios (some may fail)
    2. Successful scenarios return ProcessResult(success=True)
    3. Failed scenarios return ProcessResult(success=False, error=...)
    4. After all execution, if failures exist: raise BatchExecutionError

    Attributes:
        failed_results: List of ProcessResult objects with success=False
        failed_count: Number of failed scenarios
    """

    def __init__(self, failed_results: List[ProcessResult]):
        """
        Initialize batch execution error.

        Args:
            failed_results: List of failed ProcessResult objects
        """
        self.failed_results = failed_results
        self.failed_count = len(failed_results)

        # Build detailed error message
        message = (
            f"\n{'='*80}\n"
            f"BATCH EXECUTION COMPLETED WITH {self.failed_count} FAILED SCENARIO(S)\n"
            f"{'='*80}\n"
        )

        for idx, result in enumerate(failed_results, 1):
            message += (
                f"\n[{idx}/{self.failed_count}] Scenario: {result.scenario_name}\n"
                f"{'─'*80}\n"
                f"  Error Type: {result.error_type}\n"
                f"  Error Message: {result.error_message}\n"
                f"\n  Traceback:\n"
            )

            # Indent traceback
            if result.traceback:
                for line in result.traceback.split('\n'):
                    if line.strip():
                        message += f"    {line}\n"

            message += f"{'─'*80}\n"

        message += f"\n{'='*80}\n"

        super().__init__(message)

    def get_failed_scenario_names(self) -> List[str]:
        """Get list of failed scenario names."""
        return [r.scenario_name for r in self.failed_results]

    def get_failure_summary(self) -> str:
        """Get short summary of failures."""
        return (
            f"{self.failed_count} scenario(s) failed: "
            f"{', '.join(self.get_failed_scenario_names())}"
        )
