"""
FiniexTestingIDE - Persistence Types
Runtime domain types for the algo state persistence layer (#354) and the envelope every
carry-over store writes around its payload (#486).
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


@dataclass
class RestoreContext:
    """
    Context passed to an algo when its persisted state is being restored (#354).

    The framework measures the timing values (an algo must never read wall-clock
    itself — §9). `trading_days` is weekend-aware on markets that close on
    weekends (Forex); on 24/7 markets (crypto) it equals the calendar-day count.

    Args:
        saved_at_utc: When the snapshot was written (UTC, from the envelope)
        now_utc: Current time at restore, measured by the framework (UTC)
        age_seconds: Wall-clock age of the snapshot (now_utc - saved_at_utc)
        trading_days: Trading days between save and restore (weekend-aware on
            weekend-closing markets; == calendar days on 24/7 markets)
        weekend_aware: True if the market closes on weekends (Forex), False for 24/7
    """
    saved_at_utc: datetime
    now_utc: datetime
    age_seconds: float
    trading_days: int
    weekend_aware: bool


class PositionFeeCarryOver(BaseModel):
    """
    One fee already incurred on an open position, projected to plain values.

    The fee classes are polymorphic and hold behaviour (`calculate_cost`), which a note cannot
    carry. What is written down is therefore the INCURRED fact — type, state, time, cost — and
    that is enough for SPOT, where the list is the settled entry commission: swap accrual is
    skipped in spot mode entirely, so nothing here keeps growing after the write. A margin
    position's swap DOES keep accruing, which a flat projection would silently freeze — one of
    the reasons the book stays spot-only until #209.

    Args:
        fee_type: The FeeType value
        status: The FeeStatus value
        timestamp: When it was incurred, ISO-8601 UTC
        cost: Cost in account currency
    """
    fee_type: str
    status: str
    timestamp: str
    cost: float


class BrokerTradeCarryOver(BaseModel):
    """
    One atomic execution behind an open position, projected to plain values.

    Kept rather than dropped because the closing trade record reads it: without the executions
    the exit report loses the fill detail for the entry side, and it would lose it SILENTLY —
    the record would simply look like a position that never had any.

    Args:
        trade_id: The venue's execution id
        parent_broker_ref: The parent order's venue reference
        order_id: Our internal order id
        volume: Lots filled in this execution
        price: Price of this execution
        fee: Venue-reported fee for this execution
        fee_currency: Currency of that fee
        timestamp: Execution time, ISO-8601 UTC
        side: BUY / SELL — what this execution did
        is_maker: True for maker fills
    """
    trade_id: str
    parent_broker_ref: str
    order_id: str
    volume: float
    price: float
    fee: float
    fee_currency: str
    timestamp: str
    side: str
    is_maker: bool = False


class SubmissionCarryOver(BaseModel):
    """
    The submission-moment audit values of an open position's entry (#340).

    Args:
        tick_mid_price: Trade-channel mid price when the entry was submitted
        tick_time_msc: Tick time_msc at submission
    """
    tick_mid_price: Optional[float] = None
    tick_time_msc: Optional[int] = None


class PositionCarryOver(BaseModel):
    """
    The bot's own note about ONE open position, so the next session still has it.

    A spot position is not an object the venue holds. Kraken knows balances and orders; its
    `OpenPositions` is margin-only and empty on spot. Everything that turns 0.014 BTC into a
    position — direction, entry price, fee, "still open" — is OUR record, derived from our own
    fills. Nobody else can testify to it, so if we do not write it down it is gone, and a
    restarted bot reads its own holding as "flat" and opens a second position beside it.

    This is therefore NOT adoption from broker truth (there is none to adopt) but memory plus a
    cross-check: the venue's balance is compared against the note and a divergence is REPORTED,
    never used to overwrite it. Whether the coins are the same coins is a question of ownership
    and belongs to the capital allotment (#489); a coin carries no owner tag.

    Every field is a value that was known when the position was opened, which is why restoring
    needs neither a tick nor the canonical clock — unlike creating one.

    Args:
        position_id: Internal position id, as minted when it was opened
        symbol: The instrument
        direction: The OrderDirection value
        lots: Currently open size (a partial close reduces it)
        original_lots: Size at entry
        entry_price: The real entry price — remembered, never synthesised
        entry_time: Entry time, ISO-8601 UTC
        entry_type: The EntryType value it was opened with
        entry_tick_value: Tick value at entry
        entry_bid: Bid at entry
        entry_ask: Ask at entry
        stop_loss: Stop level, when one is set
        take_profit: Target level, when one is set
        broker_ref: The venue's reference for the entry, when known
        comment: The position's comment
        status: The PositionStatus value — a partial close must not return as untouched
        digits: Symbol decimal places as stamped at entry
        contract_size: Contract size as stamped at entry
        pip_size: Authoritative pip size for the report unit (#167)
        price_unit: The report's unit label ('pip' / 'tick')
        entry_tick_index: Tick index at entry, in the numbering of the session that opened it
        mae_pnl: Worst gross unrealized P&L seen so far (#389) — cannot be recomputed
        mfe_pnl: Best gross unrealized P&L seen so far (#389) — cannot be recomputed
        mae_price: Price at the worst excursion
        mfe_price: Price at the best excursion
        swap_accrued_until: Last rollover instant already charged, ISO-8601 UTC (#365)
        fees: Fees already incurred
        entry_trades: The atomic executions behind the entry
        entry_submission: Submission-moment audit values of the entry
    """
    position_id: str
    symbol: str
    direction: str
    lots: float
    original_lots: float
    entry_price: float
    entry_time: str
    entry_type: str
    entry_tick_value: float = 0.0
    entry_bid: float = 0.0
    entry_ask: float = 0.0
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    broker_ref: Optional[str] = None
    comment: str = ''
    status: str = ''
    digits: int = 5
    contract_size: int = 100000
    pip_size: float = 0.0
    price_unit: str = ''
    entry_tick_index: int = 0
    mae_pnl: float = 0.0
    mfe_pnl: float = 0.0
    mae_price: float = 0.0
    mfe_price: float = 0.0
    swap_accrued_until: Optional[str] = None
    fees: List[PositionFeeCarryOver] = Field(default_factory=list)
    entry_trades: List[BrokerTradeCarryOver] = Field(default_factory=list)
    entry_submission: SubmissionCarryOver = Field(default_factory=SubmissionCarryOver)


class ColdStartPayload(BaseModel):
    """
    The framework's own carry-over: what the NEXT session needs to recognise its predecessor
    (#355 Phase 2).

    Each field answers a question the successor cannot answer from broker truth alone:

    `session_keys` — the discriminators this bot has sent orders under. A resting order at the
    venue carries one of them or it does not, and that is the only thing separating "an order my
    predecessor placed" from "an order some other client placed on this account". The venue's
    open-order list is account-wide, so without this the classification has one usable branch.

    `highest_position_counter` — the largest position counter this bot has minted. The counter
    restarts at 0 with the process, so a successor would otherwise re-mint ids its predecessor
    already used. Adoption recovers the counters of orders that are still resting; this recovers
    the ones whose orders are already gone, which is what keeps ids unique per bot across a
    restart — and unique ids are what a diagnostics reader joining run records needs.

    `open_positions` — the bot's own note about what it holds. It is the one part of this
    payload that broker truth cannot replace rather than merely confirm, because a spot
    position is our derived record and not a venue object; see PositionCarryOver.

    Args:
        session_keys: Client-order-id discriminators, newest last
        highest_position_counter: The largest position counter minted so far
        open_positions: The open book at the time of writing (spot only)
    """
    session_keys: List[str] = Field(default_factory=list)
    highest_position_counter: int = 0
    open_positions: List[PositionCarryOver] = Field(default_factory=list)


class CarryOverEnvelope(BaseModel):
    """
    The header every CARRY-OVER store writes around its payload (#486).

    A carry-over is what reaches the NEXT run: it is keyed by the BOT rather than by the run, it
    is overwritten rather than accumulated, and it outlives the directory of the session that
    wrote it. That makes self-description load-bearing in a way it is not for a run artifact —
    the reader has no surrounding run to ask.

    `written_by_run_id` is PROVENANCE, never identity: it records which session last wrote the
    file, so a restored state can be traced back to the run that produced it. It is deliberately
    not part of the key — keying a carry-over by run is exactly the mistake #355 §5 rules out,
    because a restart mints a new run id and the successor could then only guess.

    Shared by the algo-state store (#354) and, when they land, cold-start recovery (#355) and
    the safety baseline (#356) — so the three do not each invent an envelope.

    Args:
        schema_version: The envelope format's own version, not the payload's
        store_id: Which registered store wrote this (the catalog's StoreId value)
        saved_at_utc: When the file was written, ISO-8601 UTC
        written_by_run_id: The run that wrote it, or None when the writer had no run identity
        profile: The bot's profile name — half of the identity check on load
        symbol: The traded symbol — the other half
        snapshot: The store's own payload, opaque to the envelope
    """
    schema_version: int
    store_id: str
    saved_at_utc: str
    written_by_run_id: Optional[str] = None
    profile: str
    symbol: str
    snapshot: Dict[str, Any] = Field(default_factory=dict)
