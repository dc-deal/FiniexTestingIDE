"""
FiniexTestingIDE - Trading Environment Package
Trade simulation, broker adapters, and portfolio management

This package provides the trading execution layer that bridges
decision logic with simulated (or live) broker execution.

:
- Added DecisionTradingAPI as public interface for Decision Logics
"""

from .broker_config import BrokerConfig, BrokerType
from .trade_simulator import TradeSimulator
from .decision_trading_api import DecisionTradingAPI
from .order_execution_engine import OrderExecutionEngine
from .portfolio_manager import PortfolioManager, Position, AccountInfo
from ..types.order_types import (
    OrderType,
    OrderDirection,
    OrderStatus,
    OrderResult,
    OrderCapabilities,
    MarketOrder,
    LimitOrder,
    StopOrder,
    StopLimitOrder,
    RejectionReason
)
from .trading_fees import (
    AbstractTradingFee,
    FeeType,
    SpreadFee,
    SwapFee,
    CommissionFee,
    MakerTakerFee,
    create_spread_fee_from_tick
)

__all__ = [
    # Core Classes
    'BrokerConfig',
    'BrokerType',
    'TradeSimulator',
    'DecisionTradingAPI',
    'OrderExecutionEngine',
    'PortfolioManager',
    'Position',
    'AccountInfo',

    # Order Types
    'OrderType',
    'OrderDirection',
    'OrderStatus',
    'OrderResult',
    'OrderCapabilities',
    'MarketOrder',
    'LimitOrder',
    'StopOrder',
    'StopLimitOrder',
    'RejectionReason',

    # Trading Fees
    'AbstractTradingFee',
    'FeeType',
    'SpreadFee',
    'SwapFee',
    'CommissionFee',
    'MakerTakerFee',
    'create_spread_fee_from_tick',
]
