"""
FiniexTestingIDE - Shared SL/TP & Limit Order Validation Tests
Reusable test classes for SL/TP trigger detection, limit order fills, and modifications.

Validates:
- TP trigger: close_reason=TP_TRIGGERED, exit_price=take_profit
- SL trigger: close_reason=SL_TRIGGERED, exit_price=stop_loss
- TradeRecord carries correct SL/TP levels
- Execution stats track sl_tp_triggered count
- Modify: modified TP triggers instead of original
- Limit fill: entry_type=LIMIT, entry_price=limit_price, maker fee
- Limit + SL/TP combo: limit fills, then SL triggers
- Modify limit: limit price changed before fill
- Stop order: entry_type=STOP, entry at market price >= stop_price
- Stop-limit order: entry_type=STOP_LIMIT, entry at limit_price
- Stop + TP combo: stop triggers, then TP closes position
- Modify stop: unreachable stop modified closer, triggers after modify
- Cancel stop: cancelled before trigger, 0 trades

Used by: sltp_limit_validation test suite
Import these classes into suite-specific test_sltp_limit_validation.py files.
"""

from typing import List

from python.framework.types.order_types import OrderDirection
from python.framework.types.portfolio_trade_record_types import EntryType, TradeRecord, CloseReason
from python.framework.types.trading_env_stats_types import ExecutionStats


# =============================================================================
# LONG TP TRIGGER
# =============================================================================

class TestLongTpTrigger:
    """Tests for LONG position where take profit triggers."""

    def test_trade_count(self, long_tp_trade_history: List[TradeRecord]):
        """Exactly one trade should be closed by TP."""
        assert len(long_tp_trade_history) == 1, (
            f"Expected 1 trade, got {len(long_tp_trade_history)}"
        )

    def test_close_reason_is_tp(self, long_tp_trade_history: List[TradeRecord]):
        """Trade should be closed by TP trigger."""
        trade = long_tp_trade_history[0]
        assert trade.close_reason == CloseReason.TP_TRIGGERED, (
            f"Expected TP_TRIGGERED, got {trade.close_reason}"
        )

    def test_direction_is_long(self, long_tp_trade_history: List[TradeRecord]):
        """Trade direction should be LONG."""
        trade = long_tp_trade_history[0]
        assert trade.direction == OrderDirection.LONG, (
            f"Expected LONG, got {trade.direction}"
        )

    def test_exit_price_equals_tp(self, long_tp_trade_history: List[TradeRecord]):
        """Exit price should equal take profit level (deterministic fill)."""
        trade = long_tp_trade_history[0]
        assert trade.exit_price == trade.take_profit, (
            f"Exit price {trade.exit_price} != take_profit {trade.take_profit}"
        )

    def test_tp_level_matches_config(self, long_tp_trade_history: List[TradeRecord]):
        """Take profit on TradeRecord should match configured level."""
        trade = long_tp_trade_history[0]
        assert trade.take_profit == 157.300, (
            f"Expected TP=157.300, got {trade.take_profit}"
        )

    def test_sl_level_matches_config(self, long_tp_trade_history: List[TradeRecord]):
        """Stop loss on TradeRecord should match configured level."""
        trade = long_tp_trade_history[0]
        assert trade.stop_loss == 156.000, (
            f"Expected SL=156.000, got {trade.stop_loss}"
        )

    def test_sl_tp_triggered_count(self, long_tp_execution_stats: ExecutionStats):
        """Execution stats should show 1 SL/TP trigger."""
        assert long_tp_execution_stats.sl_tp_triggered == 1, (
            f"Expected 1 trigger, got {long_tp_execution_stats.sl_tp_triggered}"
        )


# =============================================================================
# LONG SL TRIGGER
# =============================================================================

class TestLongSlTrigger:
    """Tests for LONG position where stop loss triggers (against downtrend)."""

    def test_trade_count(self, long_sl_trade_history: List[TradeRecord]):
        """Exactly one trade should be closed by SL."""
        assert len(long_sl_trade_history) == 1, (
            f"Expected 1 trade, got {len(long_sl_trade_history)}"
        )

    def test_close_reason_is_sl(self, long_sl_trade_history: List[TradeRecord]):
        """Trade should be closed by SL trigger."""
        trade = long_sl_trade_history[0]
        assert trade.close_reason == CloseReason.SL_TRIGGERED, (
            f"Expected SL_TRIGGERED, got {trade.close_reason}"
        )

    def test_direction_is_long(self, long_sl_trade_history: List[TradeRecord]):
        """Trade direction should be LONG."""
        trade = long_sl_trade_history[0]
        assert trade.direction == OrderDirection.LONG, (
            f"Expected LONG, got {trade.direction}"
        )

    def test_exit_price_equals_sl(self, long_sl_trade_history: List[TradeRecord]):
        """Exit price should equal stop loss level (deterministic fill)."""
        trade = long_sl_trade_history[0]
        assert trade.exit_price == trade.stop_loss, (
            f"Exit price {trade.exit_price} != stop_loss {trade.stop_loss}"
        )

    def test_sl_level_matches_config(self, long_sl_trade_history: List[TradeRecord]):
        """Stop loss on TradeRecord should match configured level."""
        trade = long_sl_trade_history[0]
        assert trade.stop_loss == 156.000, (
            f"Expected SL=156.000, got {trade.stop_loss}"
        )

    def test_sl_tp_triggered_count(self, long_sl_execution_stats: ExecutionStats):
        """Execution stats should show 1 SL/TP trigger."""
        assert long_sl_execution_stats.sl_tp_triggered == 1, (
            f"Expected 1 trigger, got {long_sl_execution_stats.sl_tp_triggered}"
        )

    def test_negative_pnl(self, long_sl_trade_history: List[TradeRecord]):
        """LONG closed at SL below entry should have negative gross P&L."""
        trade = long_sl_trade_history[0]
        assert trade.gross_pnl < 0, (
            f"Expected negative P&L for SL close, got {trade.gross_pnl}"
        )


# =============================================================================
# SHORT TP TRIGGER
# =============================================================================

class TestShortTpTrigger:
    """Tests for SHORT position where take profit triggers."""

    def test_trade_count(self, short_tp_trade_history: List[TradeRecord]):
        """Exactly one trade should be closed by TP."""
        assert len(short_tp_trade_history) == 1, (
            f"Expected 1 trade, got {len(short_tp_trade_history)}"
        )

    def test_close_reason_is_tp(self, short_tp_trade_history: List[TradeRecord]):
        """Trade should be closed by TP trigger."""
        trade = short_tp_trade_history[0]
        assert trade.close_reason == CloseReason.TP_TRIGGERED, (
            f"Expected TP_TRIGGERED, got {trade.close_reason}"
        )

    def test_direction_is_short(self, short_tp_trade_history: List[TradeRecord]):
        """Trade direction should be SHORT."""
        trade = short_tp_trade_history[0]
        assert trade.direction == OrderDirection.SHORT, (
            f"Expected SHORT, got {trade.direction}"
        )

    def test_exit_price_equals_tp(self, short_tp_trade_history: List[TradeRecord]):
        """Exit price should equal take profit level (deterministic fill)."""
        trade = short_tp_trade_history[0]
        assert trade.exit_price == trade.take_profit, (
            f"Exit price {trade.exit_price} != take_profit {trade.take_profit}"
        )

    def test_tp_level_matches_config(self, short_tp_trade_history: List[TradeRecord]):
        """Take profit on TradeRecord should match configured level."""
        trade = short_tp_trade_history[0]
        assert trade.take_profit == 156.000, (
            f"Expected TP=156.000, got {trade.take_profit}"
        )

    def test_sl_tp_triggered_count(self, short_tp_execution_stats: ExecutionStats):
        """Execution stats should show 1 SL/TP trigger."""
        assert short_tp_execution_stats.sl_tp_triggered == 1, (
            f"Expected 1 trigger, got {short_tp_execution_stats.sl_tp_triggered}"
        )


# =============================================================================
# SHORT SL TRIGGER
# =============================================================================

class TestShortSlTrigger:
    """Tests for SHORT position where stop loss triggers (against uptrend)."""

    def test_trade_count(self, short_sl_trade_history: List[TradeRecord]):
        """Exactly one trade should be closed by SL."""
        assert len(short_sl_trade_history) == 1, (
            f"Expected 1 trade, got {len(short_sl_trade_history)}"
        )

    def test_close_reason_is_sl(self, short_sl_trade_history: List[TradeRecord]):
        """Trade should be closed by SL trigger."""
        trade = short_sl_trade_history[0]
        assert trade.close_reason == CloseReason.SL_TRIGGERED, (
            f"Expected SL_TRIGGERED, got {trade.close_reason}"
        )

    def test_direction_is_short(self, short_sl_trade_history: List[TradeRecord]):
        """Trade direction should be SHORT."""
        trade = short_sl_trade_history[0]
        assert trade.direction == OrderDirection.SHORT, (
            f"Expected SHORT, got {trade.direction}"
        )

    def test_exit_price_equals_sl(self, short_sl_trade_history: List[TradeRecord]):
        """Exit price should equal stop loss level (deterministic fill)."""
        trade = short_sl_trade_history[0]
        assert trade.exit_price == trade.stop_loss, (
            f"Exit price {trade.exit_price} != stop_loss {trade.stop_loss}"
        )

    def test_sl_level_matches_config(self, short_sl_trade_history: List[TradeRecord]):
        """Stop loss on TradeRecord should match configured level."""
        trade = short_sl_trade_history[0]
        assert trade.stop_loss == 156.300, (
            f"Expected SL=156.300, got {trade.stop_loss}"
        )

    def test_sl_tp_triggered_count(self, short_sl_execution_stats: ExecutionStats):
        """Execution stats should show 1 SL/TP trigger."""
        assert short_sl_execution_stats.sl_tp_triggered == 1, (
            f"Expected 1 trigger, got {short_sl_execution_stats.sl_tp_triggered}"
        )

    def test_negative_pnl(self, short_sl_trade_history: List[TradeRecord]):
        """SHORT closed at SL above entry should have negative gross P&L."""
        trade = short_sl_trade_history[0]
        assert trade.gross_pnl < 0, (
            f"Expected negative P&L for SL close, got {trade.gross_pnl}"
        )


# =============================================================================
# MODIFY TP TRIGGER
# =============================================================================

class TestModifyTpTrigger:
    """Tests for position modification — modified TP triggers instead of original."""

    def test_trade_count(self, modify_tp_trade_history: List[TradeRecord]):
        """Exactly one trade should be closed."""
        assert len(modify_tp_trade_history) == 1, (
            f"Expected 1 trade, got {len(modify_tp_trade_history)}"
        )

    def test_close_reason_is_tp(self, modify_tp_trade_history: List[TradeRecord]):
        """Trade should be closed by TP trigger (modified TP)."""
        trade = modify_tp_trade_history[0]
        assert trade.close_reason == CloseReason.TP_TRIGGERED, (
            f"Expected TP_TRIGGERED, got {trade.close_reason}"
        )

    def test_tp_is_modified_value(self, modify_tp_trade_history: List[TradeRecord]):
        """TradeRecord should carry the modified TP, not the original."""
        trade = modify_tp_trade_history[0]
        # Original TP was 160.000, modified to 157.300
        assert trade.take_profit == 157.300, (
            f"Expected modified TP=157.300, got {trade.take_profit}"
        )

    def test_exit_price_equals_modified_tp(self, modify_tp_trade_history: List[TradeRecord]):
        """Exit price should equal the modified TP level."""
        trade = modify_tp_trade_history[0]
        assert trade.exit_price == 157.300, (
            f"Exit price {trade.exit_price} != modified TP 157.300"
        )

    def test_sl_tp_triggered_count(self, modify_tp_execution_stats: ExecutionStats):
        """Execution stats should show 1 SL/TP trigger."""
        assert modify_tp_execution_stats.sl_tp_triggered == 1, (
            f"Expected 1 trigger, got {modify_tp_execution_stats.sl_tp_triggered}"
        )


# =============================================================================
# LONG LIMIT FILL
# =============================================================================

class TestLongLimitFill:
    """Tests for LONG limit buy that fills when price drops to limit level."""

    def test_trade_count(self, long_limit_fill_trade_history: List[TradeRecord]):
        """Exactly one trade should exist."""
        assert len(long_limit_fill_trade_history) == 1, (
            f"Expected 1 trade, got {len(long_limit_fill_trade_history)}"
        )

    def test_entry_type_is_limit(self, long_limit_fill_trade_history: List[TradeRecord]):
        """Trade should have entry_type=LIMIT."""
        trade = long_limit_fill_trade_history[0]
        assert trade.entry_type == EntryType.LIMIT, (
            f"Expected LIMIT, got {trade.entry_type}"
        )

    def test_entry_price_equals_limit(self, long_limit_fill_trade_history: List[TradeRecord]):
        """Entry price should equal the configured limit price."""
        trade = long_limit_fill_trade_history[0]
        assert trade.entry_price == 156.000, (
            f"Expected entry_price=156.000, got {trade.entry_price}"
        )

    def test_direction_is_long(self, long_limit_fill_trade_history: List[TradeRecord]):
        """Trade direction should be LONG."""
        trade = long_limit_fill_trade_history[0]
        assert trade.direction == OrderDirection.LONG, (
            f"Expected LONG, got {trade.direction}"
        )

    def test_close_reason_scenario_end(self, long_limit_fill_trade_history: List[TradeRecord]):
        """Trade should be closed at scenario end (no SL/TP configured)."""
        trade = long_limit_fill_trade_history[0]
        assert trade.close_reason == CloseReason.SCENARIO_END, (
            f"Expected SCENARIO_END, got {trade.close_reason}"
        )


# =============================================================================
# SHORT LIMIT FILL
# =============================================================================

class TestShortLimitFill:
    """Tests for SHORT limit sell that fills when price rises to limit level."""

    def test_trade_count(self, short_limit_fill_trade_history: List[TradeRecord]):
        """Exactly one trade should exist."""
        assert len(short_limit_fill_trade_history) == 1, (
            f"Expected 1 trade, got {len(short_limit_fill_trade_history)}"
        )

    def test_entry_type_is_limit(self, short_limit_fill_trade_history: List[TradeRecord]):
        """Trade should have entry_type=LIMIT."""
        trade = short_limit_fill_trade_history[0]
        assert trade.entry_type == EntryType.LIMIT, (
            f"Expected LIMIT, got {trade.entry_type}"
        )

    def test_entry_price_equals_limit(self, short_limit_fill_trade_history: List[TradeRecord]):
        """Entry price should equal the configured limit price."""
        trade = short_limit_fill_trade_history[0]
        assert trade.entry_price == 157.300, (
            f"Expected entry_price=157.300, got {trade.entry_price}"
        )

    def test_direction_is_short(self, short_limit_fill_trade_history: List[TradeRecord]):
        """Trade direction should be SHORT."""
        trade = short_limit_fill_trade_history[0]
        assert trade.direction == OrderDirection.SHORT, (
            f"Expected SHORT, got {trade.direction}"
        )

    def test_close_reason_scenario_end(self, short_limit_fill_trade_history: List[TradeRecord]):
        """Trade should be closed at scenario end (no SL/TP configured)."""
        trade = short_limit_fill_trade_history[0]
        assert trade.close_reason == CloseReason.SCENARIO_END, (
            f"Expected SCENARIO_END, got {trade.close_reason}"
        )


# =============================================================================
# LIMIT FILL THEN SL TRIGGER
# =============================================================================

class TestLimitFillThenSl:
    """Tests for limit order fill followed by SL trigger."""

    def test_trade_count(self, limit_sl_trade_history: List[TradeRecord]):
        """Exactly one trade should exist."""
        assert len(limit_sl_trade_history) == 1, (
            f"Expected 1 trade, got {len(limit_sl_trade_history)}"
        )

    def test_entry_type_is_limit(self, limit_sl_trade_history: List[TradeRecord]):
        """Trade should have entry_type=LIMIT."""
        trade = limit_sl_trade_history[0]
        assert trade.entry_type == EntryType.LIMIT, (
            f"Expected LIMIT, got {trade.entry_type}"
        )

    def test_entry_price_equals_limit(self, limit_sl_trade_history: List[TradeRecord]):
        """Entry price should equal the configured limit price."""
        trade = limit_sl_trade_history[0]
        assert trade.entry_price == 156.500, (
            f"Expected entry_price=156.500, got {trade.entry_price}"
        )

    def test_close_reason_is_sl(self, limit_sl_trade_history: List[TradeRecord]):
        """Trade should be closed by SL trigger."""
        trade = limit_sl_trade_history[0]
        assert trade.close_reason == CloseReason.SL_TRIGGERED, (
            f"Expected SL_TRIGGERED, got {trade.close_reason}"
        )

    def test_exit_price_equals_sl(self, limit_sl_trade_history: List[TradeRecord]):
        """Exit price should equal stop loss level."""
        trade = limit_sl_trade_history[0]
        assert trade.exit_price == 155.800, (
            f"Expected exit_price=155.800, got {trade.exit_price}"
        )

    def test_negative_pnl(self, limit_sl_trade_history: List[TradeRecord]):
        """LONG closed at SL below entry should have negative gross P&L."""
        trade = limit_sl_trade_history[0]
        assert trade.gross_pnl < 0, (
            f"Expected negative P&L for SL close, got {trade.gross_pnl}"
        )

    def test_sl_tp_triggered_count(self, limit_sl_execution_stats: ExecutionStats):
        """Execution stats should show 1 SL/TP trigger."""
        assert limit_sl_execution_stats.sl_tp_triggered == 1, (
            f"Expected 1 trigger, got {limit_sl_execution_stats.sl_tp_triggered}"
        )


# =============================================================================
# MODIFY LIMIT PRICE FILL
# =============================================================================

class TestModifyLimitPriceFill:
    """Tests for limit order with price modified before fill."""

    def test_trade_count(self, modify_limit_trade_history: List[TradeRecord]):
        """Exactly one trade should exist."""
        assert len(modify_limit_trade_history) == 1, (
            f"Expected 1 trade, got {len(modify_limit_trade_history)}"
        )

    def test_entry_type_is_limit(self, modify_limit_trade_history: List[TradeRecord]):
        """Trade should have entry_type=LIMIT."""
        trade = modify_limit_trade_history[0]
        assert trade.entry_type == EntryType.LIMIT, (
            f"Expected LIMIT, got {trade.entry_type}"
        )

    def test_entry_price_is_modified(self, modify_limit_trade_history: List[TradeRecord]):
        """Entry price should equal the modified limit price, not original."""
        trade = modify_limit_trade_history[0]
        # Original was 155.000, modified to 156.200
        assert trade.entry_price == 156.200, (
            f"Expected modified entry_price=156.200, got {trade.entry_price}"
        )

    def test_direction_is_long(self, modify_limit_trade_history: List[TradeRecord]):
        """Trade direction should be LONG."""
        trade = modify_limit_trade_history[0]
        assert trade.direction == OrderDirection.LONG, (
            f"Expected LONG, got {trade.direction}"
        )


# =============================================================================
# STOP LONG TRIGGER
# =============================================================================

class TestStopLongTrigger:
    """Tests for STOP LONG — stop triggers when price rises above stop_price."""

    def test_trade_count(self, stop_long_trade_history: List[TradeRecord]):
        """Exactly one trade should exist."""
        assert len(stop_long_trade_history) == 1, (
            f"Expected 1 trade, got {len(stop_long_trade_history)}"
        )

    def test_entry_type_is_stop(self, stop_long_trade_history: List[TradeRecord]):
        """Trade should have entry_type=STOP."""
        trade = stop_long_trade_history[0]
        assert trade.entry_type == EntryType.STOP, (
            f"Expected STOP, got {trade.entry_type}"
        )

    def test_entry_price_at_or_above_stop(self, stop_long_trade_history: List[TradeRecord]):
        """Entry price should be at or above stop_price (market fill after trigger)."""
        trade = stop_long_trade_history[0]
        assert trade.entry_price >= 157.000, (
            f"Expected entry_price >= 157.000, got {trade.entry_price}"
        )

    def test_direction_is_long(self, stop_long_trade_history: List[TradeRecord]):
        """Trade direction should be LONG."""
        trade = stop_long_trade_history[0]
        assert trade.direction == OrderDirection.LONG, (
            f"Expected LONG, got {trade.direction}"
        )

    def test_close_reason_scenario_end(self, stop_long_trade_history: List[TradeRecord]):
        """Trade should be closed at scenario end (no SL/TP configured)."""
        trade = stop_long_trade_history[0]
        assert trade.close_reason == CloseReason.SCENARIO_END, (
            f"Expected SCENARIO_END, got {trade.close_reason}"
        )


# =============================================================================
# STOP SHORT TRIGGER
# =============================================================================

class TestStopShortTrigger:
    """Tests for STOP SHORT — stop triggers when price drops below stop_price."""

    def test_trade_count(self, stop_short_trade_history: List[TradeRecord]):
        """Exactly one trade should exist."""
        assert len(stop_short_trade_history) == 1, (
            f"Expected 1 trade, got {len(stop_short_trade_history)}"
        )

    def test_entry_type_is_stop(self, stop_short_trade_history: List[TradeRecord]):
        """Trade should have entry_type=STOP."""
        trade = stop_short_trade_history[0]
        assert trade.entry_type == EntryType.STOP, (
            f"Expected STOP, got {trade.entry_type}"
        )

    def test_entry_price_at_or_below_stop(self, stop_short_trade_history: List[TradeRecord]):
        """Entry price should be at or below stop_price (market fill after trigger)."""
        trade = stop_short_trade_history[0]
        assert trade.entry_price <= 156.200, (
            f"Expected entry_price <= 156.200, got {trade.entry_price}"
        )

    def test_direction_is_short(self, stop_short_trade_history: List[TradeRecord]):
        """Trade direction should be SHORT."""
        trade = stop_short_trade_history[0]
        assert trade.direction == OrderDirection.SHORT, (
            f"Expected SHORT, got {trade.direction}"
        )

    def test_close_reason_scenario_end(self, stop_short_trade_history: List[TradeRecord]):
        """Trade should be closed at scenario end (no SL/TP configured)."""
        trade = stop_short_trade_history[0]
        assert trade.close_reason == CloseReason.SCENARIO_END, (
            f"Expected SCENARIO_END, got {trade.close_reason}"
        )


# =============================================================================
# STOP_LIMIT LONG TRIGGER
# =============================================================================

class TestStopLimitLongTrigger:
    """Tests for STOP_LIMIT LONG — stop triggers, then fills at limit_price."""

    def test_trade_count(self, stop_limit_long_trade_history: List[TradeRecord]):
        """Exactly one trade should exist."""
        assert len(stop_limit_long_trade_history) == 1, (
            f"Expected 1 trade, got {len(stop_limit_long_trade_history)}"
        )

    def test_entry_type_is_stop_limit(self, stop_limit_long_trade_history: List[TradeRecord]):
        """Trade should have entry_type=STOP_LIMIT."""
        trade = stop_limit_long_trade_history[0]
        assert trade.entry_type == EntryType.STOP_LIMIT, (
            f"Expected STOP_LIMIT, got {trade.entry_type}"
        )

    def test_entry_price_equals_limit(self, stop_limit_long_trade_history: List[TradeRecord]):
        """Entry price should equal the configured limit price."""
        trade = stop_limit_long_trade_history[0]
        assert trade.entry_price == 157.200, (
            f"Expected entry_price=157.200, got {trade.entry_price}"
        )

    def test_direction_is_long(self, stop_limit_long_trade_history: List[TradeRecord]):
        """Trade direction should be LONG."""
        trade = stop_limit_long_trade_history[0]
        assert trade.direction == OrderDirection.LONG, (
            f"Expected LONG, got {trade.direction}"
        )

    def test_close_reason_scenario_end(self, stop_limit_long_trade_history: List[TradeRecord]):
        """Trade should be closed at scenario end (no SL/TP configured)."""
        trade = stop_limit_long_trade_history[0]
        assert trade.close_reason == CloseReason.SCENARIO_END, (
            f"Expected SCENARIO_END, got {trade.close_reason}"
        )


# =============================================================================
# STOP_LIMIT SHORT TRIGGER
# =============================================================================

class TestStopLimitShortTrigger:
    """Tests for STOP_LIMIT SHORT — stop triggers, then fills at limit_price."""

    def test_trade_count(self, stop_limit_short_trade_history: List[TradeRecord]):
        """Exactly one trade should exist."""
        assert len(stop_limit_short_trade_history) == 1, (
            f"Expected 1 trade, got {len(stop_limit_short_trade_history)}"
        )

    def test_entry_type_is_stop_limit(self, stop_limit_short_trade_history: List[TradeRecord]):
        """Trade should have entry_type=STOP_LIMIT."""
        trade = stop_limit_short_trade_history[0]
        assert trade.entry_type == EntryType.STOP_LIMIT, (
            f"Expected STOP_LIMIT, got {trade.entry_type}"
        )

    def test_entry_price_equals_limit(self, stop_limit_short_trade_history: List[TradeRecord]):
        """Entry price should equal the configured limit price."""
        trade = stop_limit_short_trade_history[0]
        assert trade.entry_price == 156.000, (
            f"Expected entry_price=156.000, got {trade.entry_price}"
        )

    def test_direction_is_short(self, stop_limit_short_trade_history: List[TradeRecord]):
        """Trade direction should be SHORT."""
        trade = stop_limit_short_trade_history[0]
        assert trade.direction == OrderDirection.SHORT, (
            f"Expected SHORT, got {trade.direction}"
        )

    def test_close_reason_scenario_end(self, stop_limit_short_trade_history: List[TradeRecord]):
        """Trade should be closed at scenario end (no SL/TP configured)."""
        trade = stop_limit_short_trade_history[0]
        assert trade.close_reason == CloseReason.SCENARIO_END, (
            f"Expected SCENARIO_END, got {trade.close_reason}"
        )


# =============================================================================
# STOP LONG THEN TP
# =============================================================================

class TestStopLongThenTp:
    """Tests for STOP LONG that triggers, then TP closes the position."""

    def test_trade_count(self, stop_tp_trade_history: List[TradeRecord]):
        """Exactly one trade should exist."""
        assert len(stop_tp_trade_history) == 1, (
            f"Expected 1 trade, got {len(stop_tp_trade_history)}"
        )

    def test_entry_type_is_stop(self, stop_tp_trade_history: List[TradeRecord]):
        """Trade should have entry_type=STOP."""
        trade = stop_tp_trade_history[0]
        assert trade.entry_type == EntryType.STOP, (
            f"Expected STOP, got {trade.entry_type}"
        )

    def test_close_reason_is_tp(self, stop_tp_trade_history: List[TradeRecord]):
        """Trade should be closed by TP trigger."""
        trade = stop_tp_trade_history[0]
        assert trade.close_reason == CloseReason.TP_TRIGGERED, (
            f"Expected TP_TRIGGERED, got {trade.close_reason}"
        )

    def test_exit_price_equals_tp(self, stop_tp_trade_history: List[TradeRecord]):
        """Exit price should equal take profit level."""
        trade = stop_tp_trade_history[0]
        assert trade.exit_price == 157.300, (
            f"Expected exit_price=157.300, got {trade.exit_price}"
        )

    def test_sl_tp_triggered_count(self, stop_tp_execution_stats: ExecutionStats):
        """Execution stats should show 1 SL/TP trigger."""
        assert stop_tp_execution_stats.sl_tp_triggered == 1, (
            f"Expected 1 trigger, got {stop_tp_execution_stats.sl_tp_triggered}"
        )


# =============================================================================
# MODIFY STOP TRIGGER
# =============================================================================

class TestModifyStopTrigger:
    """Tests for stop order with stop_price modified before trigger."""

    def test_trade_count(self, modify_stop_trade_history: List[TradeRecord]):
        """Exactly one trade should exist (triggers after modification)."""
        assert len(modify_stop_trade_history) == 1, (
            f"Expected 1 trade, got {len(modify_stop_trade_history)}"
        )

    def test_entry_type_is_stop(self, modify_stop_trade_history: List[TradeRecord]):
        """Trade should have entry_type=STOP."""
        trade = modify_stop_trade_history[0]
        assert trade.entry_type == EntryType.STOP, (
            f"Expected STOP, got {trade.entry_type}"
        )

    def test_entry_price_at_or_above_modified_stop(self, modify_stop_trade_history: List[TradeRecord]):
        """Entry price should be at or above the modified stop_price."""
        trade = modify_stop_trade_history[0]
        # Original was 158.000 (unreachable), modified to 157.000
        assert trade.entry_price >= 157.000, (
            f"Expected entry_price >= 157.000, got {trade.entry_price}"
        )

    def test_direction_is_long(self, modify_stop_trade_history: List[TradeRecord]):
        """Trade direction should be LONG."""
        trade = modify_stop_trade_history[0]
        assert trade.direction == OrderDirection.LONG, (
            f"Expected LONG, got {trade.direction}"
        )


# =============================================================================
# CANCEL STOP NO FILL
# =============================================================================

class TestCancelStopNoFill:
    """Tests for stop order cancelled before trigger — expects 0 trades."""

    def test_no_trades(self, cancel_stop_trade_history: List[TradeRecord]):
        """No trades should exist after stop order cancellation."""
        assert len(cancel_stop_trade_history) == 0, (
            f"Expected 0 trades, got {len(cancel_stop_trade_history)}"
        )


# =============================================================================
# CANCEL LIMIT NO FILL
# =============================================================================

class TestCancelLimitNoFill:
    """Tests for limit order cancelled before fill — expects 0 trades."""

    def test_no_trades(self, cancel_limit_trade_history: List[TradeRecord]):
        """No trades should exist after limit order cancellation."""
        assert len(cancel_limit_trade_history) == 0, (
            f"Expected 0 trades, got {len(cancel_limit_trade_history)}"
        )
