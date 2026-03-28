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
            f"{len(result.warning_messages)} warnings, {len(result.error_messages)} errors"
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

    _MAX_DETAIL_LINES = 10

    def _write_warnings_errors(self, result: AutoTraderResult) -> None:
        """
        Write warning/error summary with first N messages shown.

        Args:
            result: Completed AutoTraderResult
        """
        if len(result.warning_messages) > 0 or len(result.error_messages) > 0:
            self._summary_logger.info('-' * 60)

            if len(result.warning_messages) > 0:
                self._summary_logger.warning(
                    f"  Warnings:       {len(result.warning_messages)}"
                )
                self._write_message_preview(
                    result.warning_messages, len(result.warning_messages), 'warning'
                )

            if len(result.error_messages) > 0:
                self._summary_logger.error(
                    f"  Errors:         {len(result.error_messages)}"
                )
                self._write_message_preview(
                    result.error_messages, len(result.error_messages), 'error'
                )

    def _write_message_preview(
        self, messages: list, total: int, level: str
    ) -> None:
        """
        Show first N messages, then '... X more ...' if truncated.

        Args:
            messages: List of message strings
            total: Total count (may exceed len(messages))
            level: 'warning' or 'error' — determines logger method
        """
        log_fn = (
            self._summary_logger.warning if level == 'warning'
            else self._summary_logger.error
        )
        shown = messages[:self._MAX_DETAIL_LINES]
        for msg in shown:
            # Strip logger formatting (e.g., '[  0s 324ms] WARNING | message')
            clean = msg.split(' | ', 1)[-1] if ' | ' in msg else msg
            log_fn(f"    → {clean}")
        remaining = total - len(shown)
        if remaining > 0:
            log_fn(f"    ... {remaining} more ...")

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
