"""
FiniexTestingIDE - Portfolio Aggregation Types
Types for currency-grouped portfolio aggregation
"""

from dataclasses import dataclass, field
from typing import Dict, List, Optional

from python.framework.types.trading_env_types.broker_types import BrokerType


@dataclass
class DrawdownPeriod:
    """
    One drawdown period of the account's equity curve, from a peak until it is recovered (#529).

    It opens with the first sample below the running peak and closes with the first sample back at
    or above it; a new peak is the boundary, so two periods never share a fall. Its depth is the
    deepest point below the peak it fell from — an amount, and a share of that peak, which is the
    construction #497 uses for the deepest decline. The drawdown periods of established tools are
    bounded the same way (pyfolio's worst drawdown periods); a continuous losing stretch is another
    measure. The instants are on the run's own clock; one stays None where that clock was not set
    yet — a session's run end before its first pass.

    Args:
        peak: The account value the period fell from
        depth: How far below it the deepest sample lay, in account currency
        depth_pct: That depth as a share of the peak, in percent; None where the peak was not above
            zero, which leaves no share to take
        started_at: The first sample below the peak, ISO-8601 UTC
        trough_at: The deepest sample, ISO-8601 UTC
        recovered_at: The first sample back at or above the peak, ISO-8601 UTC; None while the
            period is still open
    """
    peak: float
    depth: float
    depth_pct: Optional[float]
    started_at: Optional[str]
    trough_at: Optional[str]
    recovered_at: Optional[str] = None


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

    # Risk metrics — ACCOUNT drawdown: the total account value from its own high to its
    # own low. NOT a trade's excursion, which is `TradeRecord.mae_pnl` and is measured
    # against that ONE position's entry (#389); with several positions open, or with a
    # spot holding that is a balance rather than a position, the two are different
    # numbers and neither can be derived from the other. And NOT the SAFETY reading,
    # which is measured against a configured baseline — `SafetyConfig.max_drawdown_pct`
    # is that THRESHOLD, not this measurement (#497).
    account_max_drawdown: float
    max_equity: float
    # Worst decline as a share of the peak it fell FROM, carried per tick rather than
    # derived from the two floats above — those two belong to different instants (#497).
    account_max_drawdown_pct: float

    # Calculated metrics
    win_rate: float
    # None = undefined: no losing trade, so gross profit / gross loss has no value.
    # NOT infinity — inf has no JSON form and Pydantic persists it as null (#391).
    profit_factor: Optional[float]

    # Cost breakdown
    total_spread_cost: float
    # Crossed against quotes modelled for ticks without one (#602), beside the measured spread
    total_modelled_spread_cost: float
    total_commission: float
    total_swap: float
    maker_fee: float
    taker_fee: float
    # The fees of the trades the run CLOSED — the population every trade row, booking period
    # and trade analytic sums. The breakdown above is what the run CHARGED, open positions
    # included and totalled in `fees_charged`; the two differ by the fees of what is still open.
    total_fees: float
    fees_charged: float

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
    # What this bot's own unfilled orders still claim, per currency (#489). Stamped at
    # capture because the claim lives on the EXECUTOR's order book while these stats come
    # from the portfolio — the two only meet where the run ends. Empty in margin mode.
    committed_funds: Dict[str, float] = field(default_factory=dict)
    # WHICH PERIOD the three drawdown figures above describe (#497). An AutoTrader session can
    # inherit its predecessor's peak and trough through the cold-start carry-over, so the
    # number may span a month of restarts — or one afternoon, and nothing in the figure
    # itself says which. `drawdown_carried_from` is the stamp of the inherited record ('' =
    # this session started its own curve); `drawdown_restarts` counts how many sessions it
    # now spans. Never set in the simulation: a backtest starts at its scenario start.
    drawdown_carried_from: str = ''
    drawdown_restarts: int = 0
    # When the curve BEGAN, as opposed to when it was last handed over. The report says
    # "since X · N sessions" from this; `drawdown_carried_from` is the handoff and walks
    # toward the present, so reading it as the start understates the span.
    drawdown_started_at: str = ''
    # The five deepest drawdown periods of the same curve, deepest first by share of the peak, the
    # one still open among them (#529). The first is the period whose depth `account_max_dd_pct` measures.
    account_drawdown_periods: List[DrawdownPeriod] = field(default_factory=list)
