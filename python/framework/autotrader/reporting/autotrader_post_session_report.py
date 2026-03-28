"""
FiniexTestingIDE - AutoTrader Post-Session Report
Console and file log summary printed after an AutoTrader session completes.
"""

from pathlib import Path
from typing import Optional

from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.types.autotrader_types.autotrader_result_types import AutoTraderResult


class AutotraderPostSessionReport:
    """
    Formats and outputs the AutoTrader session summary.

    Writes to both logger (file log) and flushes to console.
    Includes session statistics, portfolio P&L, order counts,
    clipping metrics, and output file locations.

    Args:
        logger: ScenarioLogger instance (writes to file + console buffer)
        session_name: Config name for display (e.g., 'btcusd_mock')
    """

    def __init__(self, logger: ScenarioLogger, session_name: str):
        self._logger = logger
        self._session_name = session_name

    def print_report(self, result: AutoTraderResult) -> None:
        """
        Print full session summary to logger and flush to console.

        Clears the console buffer first to discard per-tick noise,
        then writes summary and flushes only the summary lines.
        File output is unaffected (ScenarioLogger writes to file directly).

        Args:
            result: Completed AutoTraderResult with all statistics
        """
        # Discard per-tick console buffer — those lines belong in the file only
        self._logger.console_buffer.clear()

        self._write_summary(result)
        self._write_output_locations(result)
        self._logger.flush_buffer()

    def _write_summary(self, result: AutoTraderResult) -> None:
        """
        Write session statistics to logger.

        Args:
            result: Completed AutoTraderResult
        """
        self._logger.info('=' * 60)
        self._logger.info('📋 AutoTrader Session Summary')
        self._logger.info('=' * 60)
        self._logger.info(f"  Duration:       {result.session_duration_s:.1f}s")
        self._logger.info(f"  Ticks:          {result.ticks_processed:,}")
        self._logger.info(f"  Clipped:        {result.ticks_clipped:,}")
        self._logger.info(f"  Shutdown:       {result.shutdown_mode}")

        if result.portfolio_stats:
            pnl = result.portfolio_stats.total_profit - result.portfolio_stats.total_loss
            self._logger.info(
                f"  Balance:        {result.portfolio_stats.current_balance:.2f} "
                f"(P&L: {pnl:+.2f})"
            )

        if result.execution_stats:
            self._logger.info(
                f"  Orders:         {result.execution_stats.orders_sent} sent, "
                f"{result.execution_stats.orders_executed} executed, "
                f"{result.execution_stats.orders_rejected} rejected"
            )

        clipping = result.clipping_summary
        if clipping.total_ticks > 0:
            self._logger.info(
                f"  Clipping ratio: {clipping.clipping_ratio:.1%} "
                f"(max stale: {clipping.max_stale_ms:.1f}ms, "
                f"avg proc: {clipping.avg_processing_ms:.2f}ms)"
            )

        self._logger.info('=' * 60)

    def _write_output_locations(self, result: AutoTraderResult) -> None:
        """
        Write output file locations to logger.

        Args:
            result: Completed AutoTraderResult
        """
        run_dir: Optional[Path] = self._logger.get_log_dir()
        if run_dir is None:
            return

        self._logger.info(f"  Log directory:  {run_dir}")
        self._logger.info(f"  Session log:    autotrader_{self._session_name}.log")

        if result.trade_history:
            self._logger.info(
                f"  Trade log:      trades_{self._session_name}.csv "
                f"({len(result.trade_history)} trades)"
            )

        if result.order_history:
            self._logger.info(
                f"  Order log:      orders_{self._session_name}.csv "
                f"({len(result.order_history)} orders)"
            )
