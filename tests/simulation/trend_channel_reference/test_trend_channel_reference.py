"""
Trend Channel Reference Tests — the didactic CORE/trend_channel_reference logic (#118).

Validates that the reference logic drives the framework's full order surface end-to-end in the
backtest pipeline:
- resting LIMIT entries (limit_pullback mode) and resting STOP entries (stop_breakout mode)
- its own SL/TP from submission, never a position's level (#625)
- the always-on trailing stop is driven tick by tick in test_trend_channel_own_exits.py: these
  windows show a trailed stop only where it was the exit, and their trailed positions ran on
  into their targets
- the partial-close ladder (a position closed in more than one TradeRecord)
- multi-position stacking on the symbol (overlapping position lifetimes)

The robustness aggregation itself is covered by tests/simulation/robustness — here we assert the
strategy behaviors on the real trade history.
"""

import json
from pathlib import Path
from typing import Dict, List

from python.configuration.app_config_manager import AppConfigManager
from python.framework.types.portfolio_types.portfolio_trade_record_types import (
    CloseReason,
    EntryType,
    TradeRecord,
)
from python.framework.types.trading_env_types.order_types import OrderDirection

# The fixed entry size both fixtures use (a partial portion is strictly smaller).
LOT_SIZE = 0.1


def _by_position(trades: List[TradeRecord]) -> Dict[str, List[TradeRecord]]:
    """Group trade records by position id (a partially-closed position has several)."""
    grouped: Dict[str, List[TradeRecord]] = {}
    for t in trades:
        grouped.setdefault(t.position_id, []).append(t)
    return grouped


def _has_concurrency(trades: List[TradeRecord]) -> bool:
    """True if any two distinct positions were open at the same time (tick-index overlap)."""
    spans = {}
    for t in trades:
        lo, hi = spans.get(t.position_id, (t.entry_tick_index, t.exit_tick_index))
        spans[t.position_id] = (min(lo, t.entry_tick_index), max(hi, t.exit_tick_index))
    max_end = -1
    for start, end in sorted(spans.values(), key=lambda s: s[0]):
        if start < max_end:
            return True
        max_end = max(max_end, end)
    return False


class TestRunHealth:
    def test_limit_run_succeeds_with_trades(self, limit_process_result, limit_trades):
        assert limit_process_result.success is True
        assert len(limit_trades) > 0

    def test_stop_run_succeeds_with_trades(self, stop_process_result, stop_trades):
        assert stop_process_result.success is True
        assert len(stop_trades) > 0

    def test_no_orders_rejected(self, limit_execution_stats, stop_execution_stats):
        # Capacity + gate guards keep the logic from spamming refused orders — neither the
        # venue's rejections nor our own denials.
        for stats in (limit_execution_stats, stop_execution_stats):
            assert stats.orders_rejected == 0
            assert stats.orders_denied == 0


class TestEntryModes:
    def test_limit_mode_opens_via_limit_orders(self, limit_trades):
        assert all(t.entry_type == EntryType.LIMIT for t in limit_trades)

    def test_stop_mode_opens_via_stop_orders(self, stop_trades):
        assert all(t.entry_type == EntryType.STOP for t in stop_trades)


class TestRiskGeometry:
    def test_the_levels_are_the_logic_s_own(self, all_trades):
        """
        No position carries a level, and every stop-loss or take-profit exit names the level
        that fired (#625): the logic keeps its stop and target itself, so it runs on a venue
        whose API holds neither.
        """
        assert all_trades
        assert all(t.stop_loss is None and t.take_profit is None for t in all_trades)
        protective = [t for t in all_trades
                      if t.close_reason in (CloseReason.SL_TRIGGERED, CloseReason.TP_TRIGGERED)]
        assert protective
        assert all(t.exit_level is not None for t in protective)

    def test_sl_and_tp_triggers_both_occur(self, all_trades):
        reasons = {t.close_reason for t in all_trades}
        assert CloseReason.SL_TRIGGERED in reasons
        assert CloseReason.TP_TRIGGERED in reasons


class TestPartialClose:
    def test_position_closes_in_multiple_records(self, all_trades):
        # A partial-close ladder closes one position across more than one TradeRecord.
        grouped = _by_position(all_trades)
        assert any(len(records) > 1 for records in grouped.values())

    def test_partial_portion_is_smaller_than_entry_size(self, all_trades):
        # At least one closed portion is a fraction of the full entry size.
        assert any(t.lots < LOT_SIZE - 1e-9 for t in all_trades)


class TestMultiPosition:
    def test_positions_stack_concurrently(self, limit_trades, stop_trades):
        # max_positions > 1 lets a second position open before the first closes.
        assert _has_concurrency(limit_trades) or _has_concurrency(stop_trades)


def _last_setups(set_name: str) -> List[Path]:
    """
    The strategy files `last_setup.json` a fixture set's runs left, newest last.

    Args:
        set_name: The fixture's scenario set name

    Returns:
        Their paths
    """
    root = Path(AppConfigManager().get_file_logging_config_object().run_logs.simulation) / set_name
    return sorted(root.glob('*/strategy_files/*/last_setup.json'), key=lambda p: p.stat().st_mtime)


class TestTheStrategyKeepsItsLastSetup:
    """With `write_strategy_files` on, the last submitted setup is a file of the strategy's own."""

    def test_the_stop_run_kept_its_last_setup(self, stop_batch, stop_trades):
        setups = _last_setups('trend_channel_reference_stop_fixture')
        assert setups, 'no strategy file written'
        setup = json.loads(setups[-1].read_text(encoding='utf-8'))
        assert setup['mode'] == 'stop_breakout'
        assert setup['lower_band'] < setup['upper_band']
        assert {'entry_price', 'stop_loss', 'take_profit', 'gate', 'side'} <= set(setup)

    def test_the_limit_run_with_it_off_kept_none(self, limit_batch, limit_trades):
        assert _last_setups('trend_channel_reference_limit_fixture') == []
