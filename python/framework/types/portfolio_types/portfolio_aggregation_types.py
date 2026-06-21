"""
FiniexTestingIDE - Portfolio Aggregation Types
Types for currency-grouped portfolio aggregation
"""

from typing import Dict
from dataclasses import dataclass, field


from python.framework.types.trading_env_types.broker_types import BrokerType


@dataclass
class BasePortfolioStats:
    """
    Base portfolio statistics shared across single and aggregated views.

    Contains all common trading metrics, P&L, costs, and metadata.
    """
    broker_type: BrokerType

    # Trading statistics
    total_trades: int
    total_long_trades: int
    total_short_trades: int
    winning_trades: int
    losing_trades: int
    total_profit: float
    total_loss: float

    # Risk metrics
    max_drawdown: float
    max_equity: float

    # Calculated metrics
    win_rate: float
    profit_factor: float

    # Cost breakdown
    total_spread_cost: float
    total_commission: float
    total_swap: float
    maker_fee: float
    taker_fee: float
    total_fees: float

    # Metadata
    currency: str
    broker_name: str
    current_conversion_rate: float


@dataclass
class PortfolioStats(BasePortfolioStats):
    """
    Single scenario portfolio performance statistics.

    Adds balance tracking on top of base statistics.
    Spot mode: includes dual-balance snapshots and last price for P&L valuation.
    """
    current_balance: float
    initial_balance: float

    # Spot mode fields (empty/zero for margin — no display change)
    spot_mode: bool = False
    balances: Dict[str, float] = field(default_factory=dict)
    initial_balances: Dict[str, float] = field(default_factory=dict)
    last_price: float = 0.0
    symbol: str = ''
