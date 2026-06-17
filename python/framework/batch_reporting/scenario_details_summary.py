"""
FiniexTestingIDE - Scenario Details Summary

Renders the per-scenario execution/signal metadata **linearly** (one block per scenario)
purely from the unified report model (#393) — replaces the old box grid. Above
`scenario_detail_threshold` it collapses to a compact failures-only list (unchanged behavior).
"""

from datetime import datetime
from typing import List

from python.framework.batch_reporting.abstract_batch_summary_section import AbstractBatchSummarySection
from python.framework.types.api.report_types import ScenarioDetailsReport, ScenarioDetailsRow
from python.framework.utils.console_renderer import ConsoleRenderer
from python.framework.utils.time_utils import format_duration, format_tick_timespan


class ScenarioDetailsSummary(AbstractBatchSummarySection):
    """Per-scenario execution/signal metadata — linear presenter over the model."""

    _section_title = '🔍 SCENARIO DETAILS'

    def __init__(self, report: ScenarioDetailsReport) -> None:
        """
        Initialize scenario details summary.

        Args:
            report: The unified scenario-details report
        """
        self._report = report

    def render(self, renderer: ConsoleRenderer, scenario_detail_threshold: int = 9) -> None:
        """
        Render the per-scenario blocks (linear), or a compact list above the threshold.

        Args:
            renderer: ConsoleRenderer instance
            scenario_detail_threshold: Above this scenario count, render the compact list
        """
        self._render_section_header(renderer)
        units = self._report.units
        if not units:
            print("No scenarios")
            return
        if len(units) > scenario_detail_threshold:
            self._render_compact(units, renderer)
            return
        print()
        for idx, unit in enumerate(units, 1):
            if idx > 1:
                print()
                renderer.print_separator(width=120, char="·")
            self._render_unit(unit, renderer)

    def _render_unit(self, unit: ScenarioDetailsRow, renderer: ConsoleRenderer) -> None:
        """Render one scenario's metadata block (linear)."""
        marker = renderer.red('❌ ') if unit.status != 'success' else ''
        print(f"{marker}🔍 {renderer.bold(unit.name)} — {unit.data_source}/{unit.symbol}")

        if unit.status == 'failed':
            print(renderer.red(f"   Error: {unit.error_type or 'Unknown'}"))
            if unit.error_message:
                print(renderer.red(f"   {unit.error_message}"))
            return

        duration = format_duration(unit.execution_time_ms)
        timespan = format_tick_timespan(
            self._parse(unit.first_tick_time), self._parse(unit.last_tick_time),
            unit.tick_timespan_seconds)
        non_flat = unit.buy_signals + unit.sell_signals
        nf_pct = (non_flat / unit.ticks_processed * 100) if unit.ticks_processed else 0.0
        tr_pct = (unit.trades_requested / unit.ticks_processed * 100) if unit.ticks_processed else 0.0

        print(f"   Duration: {duration} | Ticks: {unit.ticks_processed:,} | {timespan}")
        print(
            f"   Non-Flat Sign.: {non_flat} ({nf_pct:.1f}%) | "
            f"B/S/F: {unit.buy_signals}/{unit.sell_signals}/{unit.flat_signals} | "
            f"Trades requested: {unit.trades_requested} ({tr_pct:.1f}%) | "
            f"Worker: {unit.worker_count}")
        if unit.status == 'hybrid':
            print(renderer.red("   ⚠️ CRITICAL: Errors detected"))

    def _render_compact(self, units: List[ScenarioDetailsRow], renderer: ConsoleRenderer) -> None:
        """Compact failures-only list for large scenario counts."""
        total = len(units)
        failed = [u for u in units if u.status != 'success']
        if not failed:
            print(f"  ✅ All {total} scenarios completed successfully")
        else:
            print(f"  ✅ {total - len(failed)}/{total} completed  |  ❌ {len(failed)} failed\n")
            for unit in failed:
                error = unit.error_message or unit.error_type or 'unknown error'
                print(f"  ❌  {unit.name:<40}  {error}")
        print()

    @staticmethod
    def _parse(iso: str):
        """Parse an ISO timestamp, or None when absent."""
        return datetime.fromisoformat(iso) if iso else None
