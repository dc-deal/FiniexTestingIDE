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


class ColdStartPayload(BaseModel):
    """
    The framework's own carry-over: what the NEXT session needs to recognise its predecessor
    (#355 Phase 2).

    Two fields, and each answers a question the successor cannot answer from broker truth alone:

    `session_keys` — the discriminators this bot has sent orders under. A resting order at the
    venue carries one of them or it does not, and that is the only thing separating "an order my
    predecessor placed" from "an order some other client placed on this account". The venue's
    open-order list is account-wide, so without this the classification has one usable branch.

    `highest_position_counter` — the largest position counter this bot has minted. The counter
    restarts at 0 with the process, so a successor would otherwise re-mint ids its predecessor
    already used. Adoption recovers the counters of orders that are still resting; this recovers
    the ones whose orders are already gone, which is what keeps ids unique per bot across a
    restart — and unique ids are what a diagnostics reader joining run records needs.

    Args:
        session_keys: Client-order-id discriminators, newest last
        highest_position_counter: The largest position counter minted so far
    """
    session_keys: List[str] = Field(default_factory=list)
    highest_position_counter: int = 0


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
