"""
FiniexTestingIDE - Trading Environment Types
Type definitions for trading environment statistics and account information

Contains:
- AccountInfo: Account state snapshot
- FreeAssetFunds: What is actually available of one asset, and the parts that make it
- PortfolioStats: Complete portfolio performance statistics
- ExecutionStats: Order execution statistics
- CostBreakdown: Detailed cost breakdown (spread/commission/swap)

All Dict[str, Any] types replaced with strongly-typed dataclasses.
"""

from dataclasses import dataclass
from typing import Dict, Optional


@dataclass
class AccountInfo:
    """
    Account information snapshot.

    Provides real-time account state including balance, equity,
    margin usage, and position statistics.

    Attributes:
        balance: Account balance (realized P&L)
        equity: Current equity (spot: total portfolio value; margin: balance + unrealized P&L)
        margin_used: Total margin used by open positions. **None at SPOT** — a spot account
            posts no margin, and the figure it used to carry was the branch of the margin
            formula that multiplies no price, i.e. a LOT COUNT wearing a currency label
        free_margin: Available margin for new positions. **None at SPOT** — it was derived
            from the above, so it moved with the holdings' unrealized P&L and pointed BOTH
            ways: it offered capital the account did not have when a coin rose, and withheld
            capital it did have when a coin fell. The spot question is answered by
            `AbstractTradeExecutor.get_free_entry_capital()` instead (#502)
        margin_level: Margin level percentage. **None at SPOT** — equity over a lot count
        open_positions: Number of open positions
        total_lots: Total lot size across all positions
        currency: Account currency
        leverage: Account leverage
        balances: Multi-currency balance dict (spot mode only, None in margin mode)
    """
    balance: float
    equity: float
    # Optional since 2026-09-24: three figures that only a MARGIN account has. A spot account
    # used to receive invented numbers for them — a false map is worse than a blank one, and
    # skipping them also removes a per-position broker call from a method the decision path
    # reaches every time it asks for the account.
    margin_used: Optional[float]
    free_margin: Optional[float]
    margin_level: Optional[float]
    open_positions: int
    total_lots: float
    currency: str
    leverage: int
    balances: Optional[Dict[str, float]] = None


@dataclass
class FreeAssetFunds:
    """
    How much of one asset is actually available, with the two figures that make it.

    `available` is the only number a caller should decide on. `balance` and `committed` ride
    along because the refusal message has to say WHICH of the two was short — a rejection
    reading only "available 3.00" leaves an operator unable to tell an empty account from one
    whose own resting orders claim everything.

    Attributes:
        available: balance minus committed — what a new order may actually spend
        balance: what our books hold of the asset
        committed: what this bot's own unfilled orders already claim of it (#489). Always 0.0
            in margin mode, where free margin is the equivalent quantity
    """
    available: float
    balance: float
    committed: float


@dataclass
class ExecutionStats:
    """
    Order execution statistics.

    Currency-agnostic order counts plus SL/TP triggers. Trading costs are NOT here —
    they live in CostBreakdown, the single cost source owned by the PortfolioManager.

    Attributes:
        orders_sent: Total orders submitted
        orders_executed: Orders successfully executed
        orders_rejected: Orders rejected (margin, validation, etc.)
        sl_tp_triggered: Closes triggered by stop-loss / take-profit
    """
    orders_sent: int
    orders_executed: int
    orders_rejected: int
    sl_tp_triggered: int = 0


@dataclass
class CostBreakdown:
    """
    Detailed trading cost breakdown.

    Separates trading costs by type for cost analysis and optimization.
    All costs are in account currency.

    Attributes:
        total_spread_cost: Bid-ask spread cost (implicit)
        total_commission: Broker commission (explicit)
        total_swap: Overnight interest (can be negative/positive)
        maker_fee: Maker-side trading fee (spot, liquidity-providing fills)
        taker_fee: Taker-side trading fee (spot, liquidity-taking fills)
        total_fees: Sum of all costs
    """
    total_spread_cost: float = 0
    total_commission: float = 0
    total_swap: float = 0
    maker_fee: float = 0
    taker_fee: float = 0
    total_fees: float = 0
    currency: str = ''  # Account currency (e.g., "USD", "EUR", "JPY")
