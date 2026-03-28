"""
FiniexTestingIDE - AutoTrader Post-Session Report
Console and file log summary printed after an AutoTrader session completes.
"""

from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.types.autotrader_types.autotrader_result_types import AutoTraderResult


class AutotraderPostSessionReport:
    """
    Formats and outputs the AutoTrader session summary.

    Writes to summary_logger (→ autotrader_summary.log + console flush)
    and to global_logger (→ autotrader_global.log, file only).

    Args:
        summary_logger: ScenarioLogger for summary file + console output
        global_logger: ScenarioLogger for global file (startup/shutdown log)
    """

    def __init__(self, summary_logger: ScenarioLogger, global_logger: ScenarioLogger):
        self._summary_logger = summary_logger
        self._global_logger = global_logger

    def print_report(self, result: AutoTraderResult) -> None:
        """
        Print full session summary to file and flush to console.

        The summary_logger buffer is used fresh (no per-tick noise to clear).
        After writing, flush_buffer() sends summary lines to console.

        Args:
            result: Completed AutoTraderResult with all statistics
        """
        self._write_summary(result)
        self._write_warnings_errors(result)
        self._write_output_locations(result)

        # Flush summary to console
        self._summary_logger.flush_buffer()

        # Also log a short note to global log (file only, no console)
        self._global_logger.info(
            f"📋 Session summary written — "
            f"{result.ticks_processed} ticks, "
            f"{result.warning_count} warnings, {result.error_count} errors"
        )

    def _write_summary(self, result: AutoTraderResult) -> None:
        """
        Write session statistics to summary logger.

        Args:
            result: Completed AutoTraderResult
        """
        self._summary_logger.info('=' * 60)
        self._summary_logger.info('📋 AutoTrader Session Summary')
        self._summary_logger.info('=' * 60)
        self._summary_logger.info(f"  Duration:       {result.session_duration_s:.1f}s")
        self._summary_logger.info(f"  Ticks:          {result.ticks_processed:,}")
        self._summary_logger.info(f"  Clipped:        {result.ticks_clipped:,}")
        self._summary_logger.info(f"  Shutdown:       {result.shutdown_mode}")

        if result.portfolio_stats:
            pnl = result.portfolio_stats.total_profit - result.portfolio_stats.total_loss
            self._summary_logger.info(
                f"  Balance:        {result.portfolio_stats.current_balance:.2f} "
                f"(P&L: {pnl:+.2f})"
            )

        if result.execution_stats:
            self._summary_logger.info(
                f"  Orders:         {result.execution_stats.orders_sent} sent, "
                f"{result.execution_stats.orders_executed} executed, "
                f"{result.execution_stats.orders_rejected} rejected"
            )

        clipping = result.clipping_summary
        if clipping.total_ticks > 0:
            self._summary_logger.info(
                f"  Clipping ratio: {clipping.clipping_ratio:.1%} "
                f"(max stale: {clipping.max_stale_ms:.1f}ms, "
                f"avg proc: {clipping.avg_processing_ms:.2f}ms)"
            )

    def _write_warnings_errors(self, result: AutoTraderResult) -> None:
        """
        Write warning/error summary counts.

        Args:
            result: Completed AutoTraderResult
        """
        if result.warning_count > 0 or result.error_count > 0:
            self._summary_logger.info('-' * 60)
            if result.warning_count > 0:
                self._summary_logger.warning(
                    f"  Warnings:       {result.warning_count}"
                )
            if result.error_count > 0:
                self._summary_logger.error(
                    f"  Errors:         {result.error_count}"
                )

    def _write_output_locations(self, result: AutoTraderResult) -> None:
        """
        Write output file locations to summary logger.

        Args:
            result: Completed AutoTraderResult
        """
        run_dir = self._summary_logger.get_log_dir()
        if run_dir is None:
            return

        self._summary_logger.info('-' * 60)
        self._summary_logger.info(f"  Log directory:  {run_dir}")

        if result.trade_history:
            self._summary_logger.info(
                f"  Trade log:      autotrader_trades.csv "
                f"({len(result.trade_history)} trades)"
            )

        if result.order_history:
            self._summary_logger.info(
                f"  Order log:      autotrader_orders.csv "
                f"({len(result.order_history)} orders)"
            )

        self._summary_logger.info('=' * 60)
