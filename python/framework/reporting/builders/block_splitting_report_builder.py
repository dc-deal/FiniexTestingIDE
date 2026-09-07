"""
FiniexTestingIDE - Block Splitting Report Builder

Aggregates the per-block edge facts of a Profile Run into per-symbol disposition facts +
ratios (the DERIVE stage of the block-splitting section). Derives them from the raw material
the tick loop hands over — the positions the block edge left OPEN, the trades that closed
naturally, the pending orders it discarded — so no measure is computed inside the subprocess;
the GOOD/MODERATE/HIGH/SEVERE label is a display class left to the presenter.

The measure the disposition rides on moved with #492: the block edge no longer force-closes
its open positions, so the impact is the UNREALISED P&L on what it left open rather than
realised P&L on invented exits. Counting trades marked `scenario_end` would report every
block as clean while the edge keeps cutting the same trades.
"""
from typing import Dict, List

from python.framework.types.api.report_types import BlockSplittingReport, BlockSplittingSymbolRow
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.scenario_types.scenario_set_types import SingleScenario
from python.framework.types.scenario_types.window_set_types import WindowSet


def build_block_splitting_report_from_batch(
    run_id: str,
    batch: BatchExecutionSummary,
    generator_profiles: List[WindowSet],
) -> BlockSplittingReport:
    """
    Build the block-splitting disposition report from the batch + generator window sets.

    Args:
        run_id: The run this report belongs to
        batch: The finished batch execution summary (carries the per-block raw material)
        generator_profiles: Generator window sets — the symbol → generator-mode lookup

    Returns:
        BlockSplittingReport with per-symbol rows + the cross-symbol aggregate
    """
    profile_mode = {
        ws.symbol: ws.mode
        for ws in generator_profiles
    }
    # The disposition is a Profile Run measure, and only the scenario knows whether it was
    # one. Joined by name like the robustness builder does — the batch's two lists are
    # index-synced, but the name is what both reports already key on.
    scenario_by_name: Dict[str, SingleScenario] = {
        s.name: s for s in batch.single_scenario_list or []}

    rows_by_symbol = {}
    for result in batch.process_result_list:
        if not result.success or not result.tick_loop_results:
            continue
        scenario = scenario_by_name.get(result.scenario_name)
        if scenario is None or not scenario.is_profile_run:
            continue
        # Symbol from the scenario name (e.g. "BTCUSD_vol_03" → "BTCUSD")
        parts = result.scenario_name.rsplit('_', 2)
        if len(parts) < 3:
            continue
        symbol = parts[0]

        row = rows_by_symbol.get(symbol)
        if row is None:
            row = BlockSplittingSymbolRow(
                symbol=symbol, generator_mode=profile_mode.get(symbol, 'unknown'))
            rows_by_symbol[symbol] = row

        loop = result.tick_loop_results
        positions = loop.open_positions or []
        trades = loop.trade_history or []

        row.block_count += 1
        row.open_at_boundary_trades += len(positions)
        # Unrealised, and it is a running mark: a position that never saw a tick carries
        # 0.0, which is honest — nothing valued it — rather than an invented number.
        row.open_at_boundary_pnl += sum(position.unrealized_pnl for position in positions)
        row.natural_closed_trades += len(trades)
        row.natural_closed_pnl += sum(trade.net_pnl for trade in trades)
        row.discarded_pending_orders += (
            loop.pending_stats.total_force_closed if loop.pending_stats else 0)

    rows = sorted(rows_by_symbol.values(), key=lambda r: r.symbol)
    for row in rows:
        row.total_trades = row.open_at_boundary_trades + row.natural_closed_trades
        row.total_pnl = row.open_at_boundary_pnl + row.natural_closed_pnl
        row.open_at_boundary_ratio = (
            row.open_at_boundary_trades / row.total_trades * 100) if row.total_trades else 0.0
        row.disposition_pct = (
            abs(row.open_at_boundary_pnl) / abs(row.total_pnl) * 100) if row.total_pnl else 0.0

    agg_open = sum(r.open_at_boundary_trades for r in rows)
    agg_trades = sum(r.total_trades for r in rows)
    agg_open_pnl = sum(r.open_at_boundary_pnl for r in rows)
    agg_pnl = sum(r.total_pnl for r in rows)

    return BlockSplittingReport(
        run_id=run_id,
        symbols=rows,
        agg_open_at_boundary_trades=agg_open,
        agg_total_trades=agg_trades,
        agg_open_at_boundary_ratio=(agg_open / agg_trades * 100) if agg_trades else 0.0,
        agg_disposition_pct=(abs(agg_open_pnl) / abs(agg_pnl) * 100) if agg_pnl else 0.0,
    )
