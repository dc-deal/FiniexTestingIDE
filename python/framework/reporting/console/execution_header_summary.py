"""
FiniexTestingIDE - Execution Header Summary

The top-of-report header: the EXECUTION RESULTS section header (profile-run aware) plus the
basic batch-stats line (status | scenario count | wall-clock time | parallel mode). Model-fed —
the status comes from the warnings/errors outcome, the counts + timing from the run-meta model.
"""

from python.framework.types.api.report_types import RunMetaReport, WarningsErrorsReport
from python.framework.types.rendering_types import BatchStatus
from python.framework.utils.console_renderer import ConsoleRenderer
from python.configuration.app_config_manager import AppConfigManager


class ExecutionHeaderSummary:
    """
    Top-of-report header + basic execution statistics (model-fed).
    """

    def __init__(
        self,
        run_meta_report: RunMetaReport,
        warnings_errors_report: WarningsErrorsReport,
        app_config: AppConfigManager,
    ):
        """
        Initialize the header summary.

        Args:
            run_meta_report: Run-level meta (scenario identity + timing split)
            warnings_errors_report: Warnings/errors model — the batch status reads its outcome
            app_config: Application configuration (parallel-mode display)
        """
        self._run_meta = run_meta_report
        self._warnings_errors_report = warnings_errors_report
        self._app_config = app_config

    def render(self, renderer: ConsoleRenderer):
        """
        Render the execution-results header + the basic-stats line.

        Args:
            renderer: Console renderer for the unified output
        """
        batch_status = self._calculate_batch_status()

        if self._run_meta.is_profile_run:
            profile_info = (f"{self._run_meta.scenario_count} blocks, "
                            f"{len(self._run_meta.symbols)} symbol(s)")
            renderer.section_header(f"🎉 EXECUTION RESULTS — Profile Run ({profile_info})")
        else:
            renderer.section_header("🎉 EXECUTION RESULTS")

        self._render_basic_stats(renderer, batch_status)

    def _calculate_batch_status(self) -> BatchStatus:
        """
        Calculate batch execution status from the run-level outcome (#395).

        Returns:
            BatchStatus.SUCCESS: All process_results successful
            BatchStatus.PARTIAL: Some process_results failed
            BatchStatus.FAILED: All process_results failed
        """
        outcome = self._warnings_errors_report.outcome
        total = outcome.total_units

        if total == 0:
            return BatchStatus.SUCCESS

        if outcome.failed_count == 0:
            return BatchStatus.SUCCESS
        elif outcome.failed_count == total:
            return BatchStatus.FAILED
        else:
            return BatchStatus.PARTIAL

    def _render_basic_stats(self, renderer: ConsoleRenderer, batch_status: BatchStatus):
        """
        Render basic execution statistics (top-level summary).

        Args:
            renderer: Console renderer for the unified output
            batch_status: Overall batch execution status
        """
        scenarios_count = self._run_meta.scenario_count
        exec_time = self._run_meta.execution_time_s

        # Check parallel mode
        batch_parallel = self._app_config.get_default_parallel_scenarios()
        max_parallel = self._app_config.get_default_max_parallel_scenarios()

        # Format batch status with color
        if batch_status == BatchStatus.SUCCESS:
            status_str = renderer.green("✅ Success: True")
        elif batch_status == BatchStatus.PARTIAL:
            status_str = renderer.yellow("⚠️ Success: Partial")
        else:  # FAILED
            status_str = renderer.red("❌ Success: False")

        scenarios_str = renderer.blue(f"📊 Scenarios: {scenarios_count}")
        time_str = renderer.blue(f"⏱️  Time: {exec_time:.2f}s")

        print(f"{status_str}  |  {scenarios_str}  |  {time_str}")

        # Batch mode
        mode_str = renderer.green(
            "Parallel") if batch_parallel else renderer.yellow("Sequential")
        print(f"{renderer.bold('⚙️  Batch Mode:')} {mode_str}", end="")

        if batch_parallel and scenarios_count > 1:
            concurrent = min(max_parallel, scenarios_count)
            print(f" ({concurrent} concurrent)")
        else:
            print()
