"""
FiniexTestingIDE - Block Splitting Disposition
Quantifies P&L distortion from block splitting in Profile Runs.

Rendered only for Profile Runs. Always displayed regardless of summary_detail.
Placed before Executive Summary in batch output.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from python.framework.batch_reporting.abstract_batch_summary_section import AbstractBatchSummarySection
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.process_data_types import BlockBoundaryReport
from python.framework.types.scenario_types.generator_profile_types import GeneratorProfile
from python.framework.utils.console_renderer import ConsoleRenderer


# ============================================================================
# Assessment Thresholds
# ============================================================================

DISPOSITION_GOOD = 3.0        # < 3% → GOOD
DISPOSITION_MODERATE = 10.0   # 3-10% → MODERATE
DISPOSITION_HIGH = 25.0       # 10-25% → HIGH
                              # > 25% → UNRELIABLE


@dataclass
class SymbolDisposition:
    """
    Disposition metrics aggregated per symbol.

    Args:
        symbol: Trading symbol
        generator_mode: Profile generation mode (volatility_split/continuous)
        block_count: Number of blocks for this symbol
        force_closed_trades: Total force-closed trades across blocks
        force_closed_pnl: Sum of force-close P&L
        natural_closed_trades: Total naturally closed trades
        natural_closed_pnl: Sum of natural-close P&L
        discarded_pending_orders: Total discarded pending orders
    """
    symbol: str
    generator_mode: str
    block_count: int = 0
    force_closed_trades: int = 0
    force_closed_pnl: float = 0.0
    natural_closed_trades: int = 0
    natural_closed_pnl: float = 0.0
    discarded_pending_orders: int = 0

    @property
    def total_trades(self) -> int:
        """Total trades (force-closed + natural)."""
        return self.force_closed_trades + self.natural_closed_trades

    @property
    def total_pnl(self) -> float:
        """Total P&L across all trades."""
        return self.force_closed_pnl + self.natural_closed_pnl

    @property
    def force_close_ratio(self) -> float:
        """Percentage of trades that were force-closed."""
        if self.total_trades == 0:
            return 0.0
        return (self.force_closed_trades / self.total_trades) * 100

    @property
    def disposition_pct(self) -> float:
        """
        Disposition percentage: force-close P&L distortion relative to total P&L.

        Uses absolute values to measure distortion magnitude regardless of
        whether force-closes were profitable or unprofitable.
        """
        if self.total_pnl == 0.0:
            return 0.0
        return (abs(self.force_closed_pnl) / abs(self.total_pnl)) * 100

    @property
    def assessment(self) -> str:
        """Assessment label based on disposition percentage."""
        pct = self.disposition_pct
        if pct < DISPOSITION_GOOD:
            return 'GOOD'
        elif pct < DISPOSITION_MODERATE:
            return 'MODERATE'
        elif pct < DISPOSITION_HIGH:
            return 'HIGH'
        else:
            return 'UNRELIABLE'


class BlockSplittingDisposition(AbstractBatchSummarySection):
    """
    Block splitting correctness assessment for Profile Runs.

    Aggregates BlockBoundaryReport data per symbol, calculates disposition,
    and renders assessment. Only rendered when generator_profiles are present.
    """

    _section_title = '📐 BLOCK SPLITTING DISPOSITION'

    def __init__(
        self,
        batch_execution_summary: BatchExecutionSummary,
        generator_profiles: List[GeneratorProfile]
    ):
        """
        Initialize disposition report.

        Args:
            batch_execution_summary: Batch execution results with BlockBoundaryReports
            generator_profiles: Generator profiles for metadata context
        """
        self._batch_summary = batch_execution_summary
        self._profiles = generator_profiles
        self._symbol_dispositions: Dict[str, SymbolDisposition] = {}
        self._aggregate()

    def _aggregate(self) -> None:
        """
        Aggregate BlockBoundaryReport data per symbol from process results.

        Matches ProcessResult.scenario_name back to profile metadata
        using the naming convention: {SYMBOL}_{mode}_{block_index:02d}
        """
        # Build profile lookup: symbol → (mode, block_count)
        profile_lookup: Dict[str, str] = {}
        for profile in self._profiles:
            meta = profile.profile_meta
            profile_lookup[meta.symbol] = meta.generator_mode

        # Aggregate boundary reports per symbol
        for result in self._batch_summary.process_result_list:
            if not result.success:
                continue
            if not result.tick_loop_results:
                continue

            report = result.tick_loop_results.block_boundary_report
            if not report:
                continue

            # Extract symbol from scenario name (e.g., "BTCUSD_vol_03" → "BTCUSD")
            scenario_name = result.scenario_name
            parts = scenario_name.rsplit('_', 2)
            if len(parts) < 3:
                continue
            symbol = parts[0]

            if symbol not in self._symbol_dispositions:
                mode = profile_lookup.get(symbol, 'unknown')
                self._symbol_dispositions[symbol] = SymbolDisposition(
                    symbol=symbol,
                    generator_mode=mode
                )

            disp = self._symbol_dispositions[symbol]
            disp.block_count += 1
            disp.force_closed_trades += report.force_closed_trades
            disp.force_closed_pnl += report.force_closed_pnl
            disp.natural_closed_trades += report.natural_closed_trades
            disp.natural_closed_pnl += report.natural_closed_pnl
            disp.discarded_pending_orders += report.discarded_pending_orders

    def render(self, renderer: ConsoleRenderer) -> None:
        """
        Render disposition assessment. Skips if no data.

        Args:
            renderer: Console renderer for formatting
        """
        if not self._symbol_dispositions:
            return

        self._render_section_header(renderer)

        # Per-symbol disposition
        symbols = sorted(self._symbol_dispositions.keys())

        for symbol in symbols:
            disp = self._symbol_dispositions[symbol]
            mode_short = 'vol' if disp.generator_mode == 'volatility_split' else 'cont'

            # Header line per symbol
            print(renderer.bold(
                f"  {symbol} ({mode_short}, {disp.block_count} blocks):"
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
            assessment_str = self._format_assessment(disp, renderer)
            print(f"    Disposition: {assessment_str}")
            print()

        # Aggregate disposition (if multiple symbols)
        if len(symbols) > 1:
            self._render_aggregate(renderer)

    def _render_aggregate(self, renderer: ConsoleRenderer) -> None:
        """
        Render aggregate disposition across all symbols.

        Args:
            renderer: Console renderer for formatting
        """
        total_force = sum(d.force_closed_trades for d in self._symbol_dispositions.values())
        total_trades = sum(d.total_trades for d in self._symbol_dispositions.values())
        total_force_pnl = sum(d.force_closed_pnl for d in self._symbol_dispositions.values())
        total_pnl = sum(d.total_pnl for d in self._symbol_dispositions.values())

        if total_pnl == 0.0:
            agg_pct = 0.0
        else:
            agg_pct = (abs(total_force_pnl) / abs(total_pnl)) * 100

        fc_ratio = (total_force / total_trades * 100) if total_trades > 0 else 0.0

        # Build aggregate assessment
        if agg_pct < DISPOSITION_GOOD:
            label = renderer.green('✅ GOOD')
        elif agg_pct < DISPOSITION_MODERATE:
            label = renderer.yellow('⚠️ MODERATE')
        elif agg_pct < DISPOSITION_HIGH:
            label = renderer.yellow('🟡 HIGH')
        else:
            label = renderer.red('❌ UNRELIABLE')

        renderer.print_separator(width=60, char='─')
        print(renderer.bold(
            f"  Aggregate: {total_force}/{total_trades} force-closed ({fc_ratio:.1f}%)  |  "
            f"Disposition: {agg_pct:.1f}% {label}"
        ))

    def _format_assessment(self, disp: SymbolDisposition, renderer: ConsoleRenderer) -> str:
        """
        Format disposition percentage with colored assessment label.

        Args:
            disp: Symbol disposition data
            renderer: Console renderer for coloring

        Returns:
            Formatted string like "14.6% ⚠️ MODERATE"
        """
        pct = disp.disposition_pct
        assessment = disp.assessment

        if assessment == 'GOOD':
            return f"{pct:.1f}% {renderer.green('✅ GOOD')}"
        elif assessment == 'MODERATE':
            return f"{pct:.1f}% {renderer.yellow('⚠️ MODERATE')}"
        elif assessment == 'HIGH':
            return f"{pct:.1f}% {renderer.yellow('🟡 HIGH')}"
        else:
            return f"{pct:.1f}% {renderer.red('❌ UNRELIABLE')}"
