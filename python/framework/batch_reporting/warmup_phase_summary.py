"""
FiniexTestingIDE - Warmup Phase Summary
Per-phase timing breakdown of the batch warmup pipeline

Rendered only when summary_detail is enabled.
Shows all six warmup phases with duration, percentage share,
and yellow highlight on the slowest phase.
"""

from python.framework.batch_reporting.abstract_batch_summary_section import AbstractBatchSummarySection
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.utils.console_renderer import ConsoleRenderer


class WarmupPhaseSummary(AbstractBatchSummarySection):
    """
    Renders per-phase warmup timing breakdown.

    Displayed only under summary_detail: true.
    Longest phase is highlighted yellow.
    """

    _section_title = '⏱️  WARMUP PHASE BREAKDOWN'

    def __init__(self, batch_execution_summary: BatchExecutionSummary) -> None:
        """
        Initialize warmup phase summary.

        Args:
            batch_execution_summary: Batch execution summary containing warmup_phases
        """
        self._batch_summary = batch_execution_summary

    def render(self, renderer: ConsoleRenderer) -> None:
        """
        Render warmup phase table.

        Args:
            renderer: Console renderer for formatting
        """
        phases = self._batch_summary.warmup_phases
        if not phases:
            return

        total_warmup = sum(p.duration_s for p in phases)
        if total_warmup <= 0:
            return

        self._render_section_header(renderer)

        slowest_name = max(phases, key=lambda p: p.duration_s).name

        # Column widths
        name_width = max(len(p.name) for p in phases)
        name_width = max(name_width, 20)

        for idx, phase in enumerate(phases):
            pct = phase.duration_s / total_warmup * 100
            label = f"Phase {idx}  {phase.name:<{name_width}}"
            value = f"{phase.duration_s:>7.2f}s  {pct:>5.1f}%"

            if phase.name == slowest_name:
                print(renderer.yellow(f"  {label}  {value}  ← slowest"))
            else:
                print(f"  {label}  {value}")

        renderer.print_separator(width=68)
        total_label = f"{'Total Warmup':<{name_width + 8}}"
        print(f"  {total_label}  {total_warmup:>7.2f}s  100.0%")
        print()
