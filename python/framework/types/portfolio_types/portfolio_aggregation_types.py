"""
FiniexTestingIDE - Portfolio Aggregation Types
Types for currency-grouped portfolio aggregation
"""

from dataclasses import dataclass, field
from typing import Dict, Optional

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
    # None = undefined: no losing trade, so gross profit / gross loss has no value.
    # NOT infinity — inf has no JSON form and Pydantic persists it as null (#391).
    profit_factor: Optional[float]

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

    # What the unit still HELD when it ended (#492) — a MARK at the last tick, not a
    # result. Stamped at capture because the portfolio already knows it; the wealth view
    # that combines it with the balance is derived in the report builder.
    unrealized_pnl: float = 0.0

    # Spot mode fields (empty/zero for margin — no display change)
    spot_mode: bool = False
    balances: Dict[str, float] = field(default_factory=dict)
    initial_balances: Dict[str, float] = field(default_factory=dict)
    last_price: float = 0.0
    symbol: str = ''
    # Authoritative currency split from the broker config (#265) — stamped at capture,
    # never derived from the symbol string.
    base_currency: str = ''
    quote_currency: str = ''
