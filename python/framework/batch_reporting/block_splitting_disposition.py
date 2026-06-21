"""
FiniexTestingIDE - Block Splitting Disposition
Quantifies P&L distortion from block splitting in Profile Runs.

Rendered only for Profile Runs. Always displayed regardless of summary_detail.
Placed before Executive Summary in batch output. Thin presenter over the model
(`BlockSplittingReport`) — the aggregation lives in the builder; only the
GOOD/MODERATE/HIGH/UNRELIABLE display class is applied here.
"""

from python.framework.batch_reporting.abstract_batch_summary_section import AbstractBatchSummarySection
from python.framework.types.api.report_types import BlockSplittingReport
from python.framework.utils.console_renderer import ConsoleRenderer


# ============================================================================
# Assessment Thresholds (display class)
# ============================================================================

DISPOSITION_GOOD = 3.0        # < 3% → GOOD
DISPOSITION_MODERATE = 10.0   # 3-10% → MODERATE
DISPOSITION_HIGH = 25.0       # 10-25% → HIGH
                              # > 25% → UNRELIABLE


class BlockSplittingDisposition(AbstractBatchSummarySection):
    """
    Block splitting correctness assessment for Profile Runs.

    Renders the per-symbol disposition + cross-symbol aggregate from the model.
    Only rendered when the report carries symbols (Profile Runs).
    """

    _section_title = '📐 BLOCK SPLITTING DISPOSITION'

    def __init__(self, report: BlockSplittingReport):
        """
        Initialize disposition presenter.

        Args:
            report: The built block-splitting report (per-symbol rows + aggregate)
        """
        self._report = report

    def render(self, renderer: ConsoleRenderer) -> None:
        """
        Render disposition assessment. Skips if no data.

        Args:
            renderer: Console renderer for formatting
        """
        if not self._report.symbols:
            return

        self._render_section_header(renderer)

        # Per-symbol disposition (rows are already sorted by symbol in the builder)
        for disp in self._report.symbols:
            mode_short = 'vol' if disp.generator_mode == 'volatility_split' else 'cont'

            # Header line per symbol
            print(renderer.bold(
                f"  {disp.symbol} ({mode_short}, {disp.block_count} blocks):"
            ))

            # Force-close stats
            fc_ratio_str = f"{disp.force_close_ratio:.1f}%"
            print(
                f"    Force-closed: {disp.force_closed_trades}/{disp.total_trades} "
                f"({fc_ratio_str})  |  "
                f"Force-close P&L: {disp.force_closed_pnl:+.2f}"
            )

            # Pending orders (only if any)
            if disp.discarded_pending_orders > 0:
                print(
                    f"    Discarded pending orders: {disp.discarded_pending_orders}"
                )

            # Disposition assessment
            print(f"    Disposition: {self._format_assessment(disp.disposition_pct, renderer)}")
            print()

        # Aggregate disposition (if multiple symbols)
        if len(self._report.symbols) > 1:
            self._render_aggregate(renderer)

    def _render_aggregate(self, renderer: ConsoleRenderer) -> None:
        """
        Render aggregate disposition across all symbols.

        Args:
            renderer: Console renderer for formatting
        """
        agg_pct = self._report.agg_disposition_pct
        label = self._assessment_label(agg_pct, renderer)

        renderer.print_separator(width=60, char='─')
        print(renderer.bold(
            f"  Aggregate: {self._report.agg_force_closed_trades}/{self._report.agg_total_trades} "
            f"force-closed ({self._report.agg_force_close_ratio:.1f}%)  |  "
            f"Disposition: {agg_pct:.1f}% {label}"
        ))

    def _format_assessment(self, pct: float, renderer: ConsoleRenderer) -> str:
        """
        Format disposition percentage with colored assessment label.

        Args:
            pct: Disposition percentage
            renderer: Console renderer for coloring

        Returns:
            Formatted string like "14.6% ⚠️ MODERATE"
        """
        return f"{pct:.1f}% {self._assessment_label(pct, renderer)}"

    def _assessment_label(self, pct: float, renderer: ConsoleRenderer) -> str:
        """
        Map a disposition percentage to its colored display label.

        Args:
            pct: Disposition percentage
            renderer: Console renderer for coloring

        Returns:
            Colored GOOD / MODERATE / HIGH / UNRELIABLE label
        """
        if pct < DISPOSITION_GOOD:
            return renderer.green('✅ GOOD')
        elif pct < DISPOSITION_MODERATE:
            return renderer.yellow('⚠️ MODERATE')
        elif pct < DISPOSITION_HIGH:
            return renderer.yellow('🟡 HIGH')
        else:
            return renderer.red('❌ UNRELIABLE')
