"""
FiniexTestingIDE - Trading Environment Logger
Specialized logger for trade execution events with complete traceability

Logs all trade lifecycle events:
- ORDER_SUBMITTED: Initial decision to trade
- ORDER_QUEUED: Gap simulation (server delay)
- ORDER_FILLED: Actual execution with slippage
- POSITION_OPENED: Position created with fees
- POSITION_CLOSED: Final P&L calculation
"""

import json
from datetime import datetime
from typing import Dict, Any, Optional, List
from logging import Logger
from dataclasses import asdict

from python.framework.types.order_types import OrderDirection, OrderStatus
from python.framework.types.decision_logic_types import Decision
from python.framework.trading_env.order_execution_engine import PendingOrder


class TradingEnvironmentLogger:
    """
    Specialized logger for trading environment events.

    Provides structured JSON logging for complete trade traceability:
    - Decision price vs Fill price (slippage detection)
    - Gap simulation delays (realistic execution)
    - Fee tracking (spread, commission, swap)
    - P&L calculation verification

    All events are JSON-formatted for easy parsing and verification.
    """

    def __init__(self, logger: Logger):
        """
        Initialize trading environment logger.

        Args:
            logger: ScenarioLogger instance from TradeSimulator
        """
        self.logger = logger

    # ============================================
    # Event Logging Methods
    # ============================================

    def log_order_submitted(
        self,
        tick_number: int,
        timestamp: datetime,
        order_id: str,
        decision: Decision,
        symbol: str,
        direction: OrderDirection,
        lots: float,
        stop_loss: Optional[float] = None,
        take_profit: Optional[float] = None
    ) -> None:
        """
        Log ORDER_SUBMITTED event.

        First contact with trading environment - records decision intent.

        Args:
            tick_number: Current tick number
            timestamp: Event timestamp (UTC)
            order_id: Generated order ID
            decision: Trading decision from DecisionLogic
            symbol: Trading symbol
            direction: OrderDirection.LONG or SHORT
            lots: Order size
            stop_loss: Optional SL level
            take_profit: Optional TP level
        """
        event_data = {
            "event": "ORDER_SUBMITTED",
            "tick": tick_number,
            "timestamp": timestamp.isoformat(),
            "order_id": order_id,
            "decision": decision.to_dict(),
            "order": {
                "symbol": symbol,
                "direction": direction.value,
                "lots": lots,
                "stop_loss": stop_loss,
                "take_profit": take_profit
            }
        }

        self.logger.verbose(f"TRADE_EVENT: {json.dumps(event_data, indent=2)}")

    def log_order_queued(
        self,
        tick_number: int,
        order_id: str,
        api_delay: int,
        exec_delay: int,
        total_delay: int,
        placed_at_tick: int,
        fill_at_tick: int,
        seeds: Dict[str, int]
    ) -> None:
        """
        Log ORDER_QUEUED event.

        Critical for gap simulation - shows artificial server delay.

        Args:
            tick_number: Current tick number
            order_id: Order identifier
            api_delay: API latency delay (ticks)
            exec_delay: Market execution delay (ticks)
            total_delay: Combined delay (ticks)
            placed_at_tick: Order placement tick
            fill_at_tick: Expected fill tick
            seeds: Random seeds used for delays
        """
        event_data = {
            "event": "ORDER_QUEUED",
            "tick": tick_number,
            "order_id": order_id,
            "delays": {
                "api_latency": api_delay,
                "market_execution": exec_delay,
                "total_delay": total_delay
            },
            "timing": {
                "placed_at_tick": placed_at_tick,
                "fill_at_tick": fill_at_tick
            },
            "seeds": seeds
        }

        self.logger.debug(f"TRADE_EVENT: {json.dumps(event_data, indent=2)}")

    def log_order_filled(
        self,
        tick_number: int,
        timestamp: datetime,
        order_id: str,
        fill_price: float,
        decision_price: float,
        decision_tick: int,
        market_state: Dict[str, float]
    ) -> None:
        """
        Log ORDER_FILLED event.

        Shows actual execution with slippage (decision price vs fill price).

        Args:
            tick_number: Current tick number
            timestamp: Fill timestamp (UTC)
            order_id: Order identifier
            fill_price: Actual execution price
            decision_price: Original decision price
            decision_tick: Tick when decision was made
            market_state: Current bid/ask/spread
        """
        price_diff = fill_price - decision_price
        ticks_delayed = tick_number - decision_tick

        event_data = {
            "event": "ORDER_FILLED",
            "tick": tick_number,
            "timestamp": timestamp.isoformat(),
            "order_id": order_id,
            "execution": {
                "fill_price": fill_price,
                "decision_price": decision_price,
                "price_diff": price_diff,
                "fill_tick": tick_number,
                "decision_tick": decision_tick,
                "ticks_delayed": ticks_delayed
            },
            "market_state": market_state
        }

        self.logger.debug(f"TRADE_EVENT: {json.dumps(event_data, indent=2)}")

    def log_position_opened(
        self,
        tick_number: int,
        position_id: str,
        symbol: str,
        direction: OrderDirection,
        lots: float,
        open_price: float,
        fees: Dict[str, Any],
        margin_used: float,
        account_state: Dict[str, float]
    ) -> None:
        """
        Log POSITION_OPENED event.

        Records position creation with all costs and margin requirements.

        Args:
            tick_number: Current tick number
            position_id: Position identifier
            symbol: Trading symbol
            direction: Position direction
            lots: Position size
            open_price: Entry price
            fees: Fee breakdown (spread, commission, swap)
            margin_used: Margin required for position
            account_state: Account balance, equity, free margin
        """
        event_data = {
            "event": "POSITION_OPENED",
            "tick": tick_number,
            "position_id": position_id,
            "symbol": symbol,
            "direction": direction.value,
            "lots": lots,
            "open_price": open_price,
            "fees": fees,
            "margin_used": margin_used,
            "account_state": account_state
        }

        self.logger.debug(f"TRADE_EVENT: {json.dumps(event_data, indent=2)}")

    def log_position_closed(
        self,
        tick_number: int,
        timestamp: datetime,
        position_id: str,
        close_reason: str,
        open_tick: int,
        open_price: float,
        open_timestamp: datetime,
        close_price: float,
        gross_pnl: float,
        total_fees: float,
        net_pnl: float,
        holding_ticks: int,
        holding_duration_seconds: float
    ) -> None:
        """
        Log POSITION_CLOSED event.

        Final P&L calculation with complete trade statistics.

        Args:
            tick_number: Current tick number
            timestamp: Close timestamp (UTC)
            position_id: Position identifier
            close_reason: Reason for close (manual, auto-close, SL, TP)
            open_tick: Opening tick number
            open_price: Entry price
            open_timestamp: Entry timestamp
            close_price: Exit price
            gross_pnl: P&L before fees
            total_fees: Total trading costs
            net_pnl: Final P&L after fees
            holding_ticks: Ticks position was open
            holding_duration_seconds: Real time duration
        """
        event_data = {
            "event": "POSITION_CLOSED",
            "tick": tick_number,
            "timestamp": timestamp.isoformat(),
            "position_id": position_id,
            "close_reason": close_reason,
            "open_data": {
                "open_tick": open_tick,
                "open_price": open_price,
                "open_timestamp": open_timestamp.isoformat()
            },
            "close_data": {
                "close_tick": tick_number,
                "close_price": close_price,
                "close_timestamp": timestamp.isoformat()
            },
            "pnl": {
                "gross_pnl": gross_pnl,
                "total_fees": total_fees,
                "net_pnl": net_pnl
            },
            "holding_time": {
                "ticks": holding_ticks,
                "duration_seconds": holding_duration_seconds
            }
        }

        self.logger.debug(f"TRADE_EVENT: {json.dumps(event_data, indent=2)}")

    # ============================================
    # Helper Methods for Object Serialization
    # ============================================

    @staticmethod
    def serialize_pending_order(pending_order: PendingOrder) -> Dict[str, Any]:
        """
        Serialize PendingOrder to dict.

        Handles nested OrderDirection enum.

        Args:
            pending_order: PendingOrder instance

        Returns:
            Serialized dict
        """
        return {
            "order_id": pending_order.order_id,
            "placed_at_tick": pending_order.placed_at_tick,
            "fill_at_tick": pending_order.fill_at_tick,
            "symbol": pending_order.symbol,
            "direction": pending_order.direction.value,
            "lots": pending_order.lots,
            "order_kwargs": pending_order.order_kwargs
        }

    @staticmethod
    def serialize_position(position) -> Dict[str, Any]:
        """
        Serialize Position to dict.

        Handles nested objects:
        - OrderDirection enum
        - PositionStatus enum
        - datetime objects
        - AbstractTradingFee list

        Args:
            position: Position instance

        Returns:
            Serialized dict
        """
        # Import here to avoid circular dependency
        from python.framework.trading_env.portfolio_manager import Position

        return {
            "position_id": position.position_id,
            "symbol": position.symbol,
            "direction": position.direction.value,
            "lots": position.lots,
            "entry_price": position.entry_price,
            "entry_time": position.entry_time.isoformat(),
            "stop_loss": position.stop_loss,
            "take_profit": position.take_profit,
            "current_price": position.current_price,
            "unrealized_pnl": position.unrealized_pnl,
            "status": position.status.value,
            "comment": position.comment,
            "magic_number": position.magic_number,
            "close_time": position.close_time.isoformat() if position.close_time else None,
            "close_price": position.close_price,
            "total_fees": position.get_total_fees(),
            "fees": [
                {
                    "type": fee.fee_type.value,
                    "cost": fee.cost,
                    "metadata": fee.metadata
                }
                for fee in position.fees
            ]
        }

    @staticmethod
    def serialize_account_info(account_info) -> Dict[str, float]:
        """
        Serialize AccountInfo to dict.

        Args:
            account_info: AccountInfo dataclass

        Returns:
            Serialized dict
        """
        return asdict(account_info)
