"""
Shared Batch Health Test
Validates that all scenarios in a batch completed without errors.

Catches runtime errors (e.g. AttributeError, TypeError) that would
otherwise only appear in batch summary logs and go unnoticed.

Used by: all suites that run batch_execution_summary
"""

from python.framework.types.batch_execution_types import BatchExecutionSummary


class TestBatchHealth:
    """Validates all scenarios in the batch executed successfully."""

    def test_all_scenarios_succeeded(
        self,
        batch_execution_summary: BatchExecutionSummary
    ):
        """Every scenario must complete without errors."""
        failed = []
        for pr in batch_execution_summary.process_result_list:
            if not pr.success:
                failed.append(
                    f"{pr.scenario_name}: {pr.error_type} — {pr.error_message}"
                )

        assert not failed, (
            f"{len(failed)} scenario(s) failed:\n" +
            "\n".join(f"  - {f}" for f in failed)
        )
