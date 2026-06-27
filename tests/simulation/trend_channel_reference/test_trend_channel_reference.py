"""
Trend Channel Reference Tests — the didactic CORE/trend_channel_reference logic (#118).

Validates that the reference logic drives the framework's full order surface end-to-end in the
backtest pipeline:
- resting LIMIT entries (limit_pullback mode) and resting STOP entries (stop_breakout mode)
- SL/TP set at submission on every position
- the always-on trailing stop (proven by a stop-loss that closed a trade in profit)
- the partial-close ladder (a position closed in more than one TradeRecord)
- multi-position stacking on the symbol (overlapping position lifetimes)

The robustness aggregation itself is covered by tests/simulation/robustness — here we assert the
strategy behaviors on the real trade history.
"""

from typing import Dict, List

from python.framework.types.portfolio_types.portfolio_trade_record_types import (
    CloseReason, EntryType, TradeRecord)
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
        # Capacity + gate guards keep the logic from spamming rejected orders.
        assert limit_execution_stats.orders_rejected == 0
        assert stop_execution_stats.orders_rejected == 0


class TestEntryModes:
    def test_limit_mode_opens_via_limit_orders(self, limit_trades):
        assert all(t.entry_type == EntryType.LIMIT for t in limit_trades)

    def test_stop_mode_opens_via_stop_orders(self, stop_trades):
        assert all(t.entry_type == EntryType.STOP for t in stop_trades)


class TestRiskGeometry:
    def test_every_position_has_sl_and_tp(self, all_trades):
        assert all_trades
        assert all(t.stop_loss is not None and t.take_profit is not None for t in all_trades)

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


class TestTrailingStop:
    def test_trailing_stop_can_close_in_profit(self, all_trades):
        # An SL that closes a trade in profit can only happen if the always-on trailing
        # stop ratcheted it past breakeven (the initial SL is always at a loss).
        trailed = [
            t for t in all_trades
            if t.close_reason == CloseReason.SL_TRIGGERED and t.gross_pnl > 0
        ]
        assert trailed, "no SL-triggered trade closed in profit — trailing did not ratchet"
        for t in trailed:
            if t.direction == OrderDirection.LONG:
                assert t.stop_loss > t.entry_price   # LONG SL trailed above entry
            else:
                assert t.stop_loss < t.entry_price   # SHORT SL trailed below entry


class TestMultiPosition:
    def test_positions_stack_concurrently(self, limit_trades, stop_trades):
        # max_positions > 1 lets a second position open before the first closes.
        assert _has_concurrency(limit_trades) or _has_concurrency(stop_trades)
