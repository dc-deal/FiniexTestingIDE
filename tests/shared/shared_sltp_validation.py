"""
FiniexTestingIDE - Shared SL/TP Validation Tests
Reusable test classes for SL/TP trigger detection and position modification.

Validates:
- TP trigger: close_reason=TP_TRIGGERED, exit_price=take_profit
- SL trigger: close_reason=SL_TRIGGERED, exit_price=stop_loss
- TradeRecord carries correct SL/TP levels
- Execution stats track sl_tp_triggered count
- Modify: modified TP triggers instead of original

Used by: sltp_validation test suite
Import these classes into suite-specific test_sltp_validation.py files.
"""

from typing import List

from python.framework.types.order_types import OrderDirection
from python.framework.types.portfolio_trade_record_types import TradeRecord, CloseReason
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
