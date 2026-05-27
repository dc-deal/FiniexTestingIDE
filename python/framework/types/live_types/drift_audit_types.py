"""
FiniexTestingIDE - Drift Audit Types (#327)
Domain types for the read-only drift telemetry channel that compares
locally-computed fee/volume/price values against broker-reported truth
from #326 per-execution BrokerTrade records.

Used by DriftAuditor (live executor) for snapshot/comparison state and
by AutoTraderDisplayStats for the Audit footer counters.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Optional

from python.framework.types.trading_env_types.order_types import OrderDirection


class DriftType(Enum):
    """Drift category. Each fully-filled order may produce up to one record per type."""
    FEE = 'fee'
    VOLUME = 'volume'
    PRICE = 'price'      # Kraken-intra-reporting consistency (QueryOrder vs QueryTrades)
    SLIPPAGE = 'slippage'  # Trade-channel mid at submission vs broker fill price (#340)


@dataclass
class DriftRecord:
    """
    One drift comparison result for one (order, drift_type) pair.

    Args:
        timestamp: When the comparison was completed (UTC, tz-aware)
        order_id: Internal order_id
        broker_ref: Broker-side order reference (Kraken txid, etc.)
        symbol: Trading symbol
        drift_type: FEE / VOLUME / PRICE
        local_value: Locally-computed value (synthetic fee, requested lots, trade-channel avg price)
        broker_value: Broker-reported truth from trades_query response
        absolute_delta: abs(local_value - broker_value)
        relative_delta_pct: Relative delta in percent
        threshold_exceeded: True if relative_delta_pct exceeded the configured threshold
        is_structural: True for PRICE drift on trade-channel feeds — counts as empirical
            data, not as a bug signal (see #244)
        fee_currency: Currency of the fee (FEE drift only)
    """
    timestamp: datetime
    order_id: str
    broker_ref: str
    symbol: str
    drift_type: DriftType
    local_value: float
    broker_value: float
    absolute_delta: float
    relative_delta_pct: float
    threshold_exceeded: bool
    is_structural: bool = False
    fee_currency: Optional[str] = None


@dataclass
class AuditContext:
    """
    Snapshot of the synthetic state captured at outcome time.

    Held in DriftAuditor._pending_audits between the outcome-listener
    invocation (where it is created) and the trades-response consumer
    invocation (where it is popped and compared).

    Args:
        order_id: Internal order_id (also the dict key)
        broker_ref: Broker-side order reference
        symbol: Trading symbol
        direction: Order direction (LONG/SHORT)
        requested_lots: Lots requested by the algo at submit
        synthetic_cumulative_fee: pending.cumulative_fee at outcome time (local fee model)
        synthetic_cumulative_avg_price: pending.cumulative_avg_price at outcome time
        synthetic_cumulative_filled_lots: pending.cumulative_filled_lots at outcome time
        fee_currency: Currency of the synthetic fee (from pending.trades[0].fee_currency)
        submission_tick_mid_price: Trade-channel mid price observed at submission
            (None for synthetic cleanup pendings — SLIPPAGE compare is skipped). #340
    """
    order_id: str
    broker_ref: str
    symbol: str
    direction: OrderDirection
    requested_lots: float
    synthetic_cumulative_fee: float
    synthetic_cumulative_avg_price: float
    synthetic_cumulative_filled_lots: float
    fee_currency: Optional[str] = None
    submission_tick_mid_price: Optional[float] = None


@dataclass
class DriftAuditSummary:
    """
    Aggregate counters and history for one DriftAuditor session.

    Surfaced at session exit (one-line summary) and via
    DriftAuditor.get_summary() for analysis.

    Args:
        total_orders_audited: Number of orders that completed audit comparison
        fee_events: Count of FEE drift records that exceeded threshold
        volume_events: Count of VOLUME drift records that exceeded threshold
        price_events: Count of PRICE drift records that exceeded threshold
        slippage_events: Count of SLIPPAGE drift records that exceeded threshold (#340)
        max_fee_drift_pct: Largest observed FEE drift relative delta
        max_volume_drift_pct: Largest observed VOLUME drift relative delta
        max_price_drift_pct: Largest observed PRICE drift relative delta
        max_slippage_drift_pct: Largest observed SLIPPAGE drift relative delta (#340)
        records: All DriftRecord instances produced this session
    """
    total_orders_audited: int = 0
    fee_events: int = 0
    volume_events: int = 0
    price_events: int = 0
    slippage_events: int = 0
    max_fee_drift_pct: float = 0.0
    max_volume_drift_pct: float = 0.0
    max_price_drift_pct: float = 0.0
    max_slippage_drift_pct: float = 0.0
    records: List[DriftRecord] = field(default_factory=list)
