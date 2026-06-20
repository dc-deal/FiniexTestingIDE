"""
FiniexTestingIDE - AutoTrader Post-Session Report
Console and file log summary printed after an AutoTrader session completes.
"""

from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.types.api.report_types import BrokerReport, TradeHistoryReport
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

    def print_report(
        self, result: AutoTraderResult, trade_report: TradeHistoryReport = None,
        broker_report: BrokerReport = None) -> None:
        """
        Print full session summary to file and flush to console.

        The summary_logger buffer is used fresh (no per-tick noise to clear).
        After writing, flush_buffer() sends summary lines to console.

        Args:
            result: Completed AutoTraderResult with all statistics
            trade_report: Unified trade-history report — its #389 analytics line is
                appended to the headline (model-sourced, #393)
            broker_report: Unified broker-configuration report — rendered as a compact
                broker/symbol line (the full table stays in broker.json / API, #391)
        """
        self._write_summary(result, trade_report)
        self._write_broker(broker_report)
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

    def _write_summary(
        self, result: AutoTraderResult, trade_report: TradeHistoryReport = None) -> None:
        """
        Write session statistics to summary logger.

        Args:
            result: Completed AutoTraderResult
            trade_report: Unified trade-history report (for the #389 analytics line)
        """
        self._summary_logger.info('=' * 60)
        self._summary_logger.info('📋 AutoTrader Session Summary')
        self._summary_logger.info('=' * 60)
        self._summary_logger.info(f"  Duration:       {result.session_duration_s:.1f}s")
        self._summary_logger.info(f"  Ticks:          {result.ticks_processed:,}")
        self._summary_logger.info(f"  Clipped:        {result.ticks_clipped:,}")
        self._summary_logger.info(f"  Shutdown:       {result.shutdown_mode}")
        if result.shutdown_mode == 'emergency' and result.emergency_reason:
            self._summary_logger.error(
                f"  ❌ EMERGENCY CAUSE: {result.emergency_reason}"
            )

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

        # Trade analytics (#389/#393) — model-sourced, one line per account currency
        # (a live session is normally one). The big per-trade table stays sim-only / API.
        for a in (trade_report.analytics if trade_report else []):
            self._summary_logger.info(
                f"  Analytics:      expectancy {a.expectancy:+.3f}R | "
                f"win-R {a.avg_win_r:+.2f} / loss-R {a.avg_loss_r:+.2f} | "
                f"R-trades {a.r_trade_count}/{a.trade_count} ({a.currency})"
            )

        clipping = result.clipping_summary
        if clipping.total_ticks > 0:
            self._summary_logger.info(
                f"  Clipping ratio: {clipping.clipping_ratio:.1%} "
                f"(max stale: {clipping.max_stale_ms:.1f}ms, "
                f"avg proc: {clipping.avg_processing_ms:.2f}ms)"
            )

    def _write_broker(self, broker_report: BrokerReport = None) -> None:
        """
        Write a compact broker/symbol line per broker (model-fed, #391).

        The full broker table (all symbol columns) lives in broker.json / the API; the
        live summary stays compact — same philosophy as the one-line analytics.

        Args:
            broker_report: Unified broker-configuration report (skipped if empty)
        """
        if not broker_report or not broker_report.units:
            return

        self._summary_logger.info('-' * 60)
        for unit in broker_report.units:
            hedging = '✅' if unit.hedging_allowed else '❌'
            hash_tag = f" | [{unit.config_hash}]" if unit.config_hash else ''
            self._summary_logger.info(
                f"  Broker:         {unit.company} ({unit.trade_mode}) | {unit.market_type} | "
                f"1:{unit.leverage} | margin {unit.margin_mode} | "
                f"MC/SO {unit.margin_call_level}/{unit.stopout_level} | hedging {hedging}{hash_tag}"
            )
            for sym in unit.symbols:
                self._summary_logger.info(
                    f"  Symbol:         {sym.symbol} ({sym.base_currency}/{sym.quote_currency}) | "
                    f"lots {sym.volume_min}-{sym.volume_max} | tick {sym.tick_size} | "
                    f"contract {sym.contract_size} | swap L/S {sym.swap_long}/{sym.swap_short}"
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

        if result.trade_history or result.order_history:
            trades_n = len(result.trade_history) if result.trade_history else 0
            orders_n = len(result.order_history) if result.order_history else 0
            self._summary_logger.info(
                f"  Event log:      events.csv "
                f"({trades_n} trades, {orders_n} orders)"
            )

        self._summary_logger.info('=' * 60)
