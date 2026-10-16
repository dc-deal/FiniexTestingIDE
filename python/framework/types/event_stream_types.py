"""
Trade-activity event-stream types (#330 / #233) — the rows of a run's `events.csv`.

One row per event, discriminated by its type; the writer builds them from the finished trade and
order records.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Optional

from python.framework.types.trading_env_types.order_types import OrderDirection, OrderSide


class TradeEventType(Enum):
    """
    Discriminator for event-stream CSV rows.

    Built from the finished trade and order records, so it holds what those records hold. An
    order's acceptance by the venue and its amends are not among them — they are steps in the
    order-event stream (#362), which records every transition as it happens.
    """
    ORDER_SUBMIT = 'ORDER_SUBMIT'        # Trigger sent (algo decision)
    ORDER_REJECT = 'ORDER_REJECT'        # Refused — by us (status denied) or by the venue (rejected)
    FILL = 'FILL'                        # One BrokerTrade — atomic execution
    POSITION_OPEN = 'POSITION_OPEN'      # _fill_open_order finalized
    CLOSE_SUBMIT = 'CLOSE_SUBMIT'        # Close trigger sent
    POSITION_CLOSE = 'POSITION_CLOSE'    # _fill_close_order finalized (full or partial)
    ORDER_CANCEL = 'ORDER_CANCEL'        # Active order cancelled
    ORDER_END = 'ORDER_END'              # Ended unfilled and unrefused: expired, undelivered, unaccounted — the status says which


@dataclass
class TradeEvent:
    """
    One event row in the long-format CSV. Optional fields stay empty per type.

    direction (LONG/SHORT) is the *position view* — populated on POSITION_OPEN
    and POSITION_CLOSE events. side (BUY/SELL) is the *trade-event view* —
    populated on FILL events (one row per BrokerTrade). The two columns are
    mutually exclusive per row: FIX-style separation of OrdSide vs PositionSide.
    """
    ts: datetime
    event_type: TradeEventType
    order_id: str = ''
    position_id: str = ''
    trade_id: str = ''
    broker_ref: str = ''
    direction: Optional[OrderDirection] = None
    side: Optional[OrderSide] = None
    lots: Optional[float] = None
    price: Optional[float] = None
    fee: Optional[float] = None
    fee_currency: str = ''
    status: str = ''
    close_type: str = ''
    close_reason: str = ''
    is_maker: Optional[bool] = None
    # #340 — Submission slippage. Populated on ORDER_SUBMIT and CLOSE_SUBMIT
    # rows (the algo decision moments). Empty on FILL / POSITION_* rows.
    submission_tick_mid_price: Optional[float] = None
    submission_tick_time_msc: Optional[int] = None
    notes: str = ''
