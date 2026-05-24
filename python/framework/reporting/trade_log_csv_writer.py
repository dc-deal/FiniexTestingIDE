"""
FiniexTestingIDE - Trade Activity Event-Stream CSV Writer (#330 / #233)

Long-format event-stream CSV (FIX ExecutionReport style) — one file per
session (AutoTrader) or per scenario (sim). Replaces the previous two-file
AutoTrader format (autotrader_orders.csv + autotrader_trades.csv) and brings
the sim pipeline to parity (which had no CSV export before).

The schema is one row per event; the event_type column is the discriminator.
Downstream tools (Pandas, Excel) reconstruct per-domain views by
groupby('event_type'). See docs/architecture/trade_execution_visibility.md
(planned) for the full event-type taxonomy.

This writer takes the post-loop trade_history + order_history as input and
walks them chronologically to emit synthetic events. No inline tick-loop
instrumentation needed — keeps the hot path untouched.
"""

import csv
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import List, Optional

from python.framework.types.portfolio_types.portfolio_trade_record_types import CloseType, TradeRecord
from python.framework.types.trading_env_types.broker_trade_types import BrokerTrade
from python.framework.types.trading_env_types.order_types import OrderAction, OrderDirection, OrderResult, OrderSide, OrderStatus


class EventType(Enum):
    """Discriminator for event-stream CSV rows."""
    ORDER_SUBMIT = 'ORDER_SUBMIT'        # Trigger sent (algo decision)
    ORDER_ACCEPT = 'ORDER_ACCEPT'        # Broker assigned broker_ref (LIMIT/STOP)
    ORDER_REJECT = 'ORDER_REJECT'        # Broker or guard rejected
    FILL = 'FILL'                        # One BrokerTrade — atomic execution
    POSITION_OPEN = 'POSITION_OPEN'      # _fill_open_order finalized
    CLOSE_SUBMIT = 'CLOSE_SUBMIT'        # Close trigger sent
    POSITION_CLOSE = 'POSITION_CLOSE'    # _fill_close_order finalized (full or partial)
    ORDER_CANCEL = 'ORDER_CANCEL'        # Active order cancelled
    ORDER_MODIFY = 'ORDER_MODIFY'        # Active order modified


# Canonical column order. Stable contract for downstream consumers.
EVENT_FIELDS = (
    'ts', 'event_type', 'order_id', 'position_id', 'trade_id',
    'broker_ref', 'direction', 'side', 'lots', 'price', 'fee', 'fee_currency',
    'status', 'close_type', 'close_reason', 'is_maker', 'notes',
)


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
    event_type: EventType
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
    notes: str = ''


class EventStreamWriter:
    """
    Long-format event-stream CSV writer.

    Constructed with a run_dir + a list of events. Events are produced
    post-loop by from_autotrader_result() or from_sim_result(). flush()
    sorts by timestamp and writes one CSV row per event.

    Args:
        run_dir: Directory to write the CSV into. If None, flush is a no-op
            (matches the file-logging-disabled convention of ScenarioSetUtils).
        events: Pre-built event list (typically from a from_* classmethod).
    """

    def __init__(self, run_dir: Optional[Path], events: List[TradeEvent]):
        self._run_dir = run_dir
        self._events = events

    @classmethod
    def from_autotrader_result(
        cls,
        trade_history: List[TradeRecord],
        order_history: List[OrderResult],
        run_dir: Optional[Path],
    ) -> 'EventStreamWriter':
        """
        Build an EventStreamWriter from an AutoTrader session's terminal state.

        Mirrors the data the previous AutotraderCsvFileReport consumed —
        trade_history + order_history are fully populated at session end.

        Args:
            trade_history: All completed TradeRecord (full + partial closes)
            order_history: All OrderResult (multiple statuses per order_id)
            run_dir: Session log directory (e.g. logs/autotrader/<name>/<ts>/)

        Returns:
            EventStreamWriter ready to flush.
        """
        events = _build_events(trade_history, order_history)
        return cls(run_dir, events)

    @classmethod
    def from_sim_result(
        cls,
        trade_history: List[TradeRecord],
        order_history: List[OrderResult],
        run_dir: Optional[Path],
    ) -> 'EventStreamWriter':
        """
        Build an EventStreamWriter from a sim ProcessResult's terminal state.

        Identical reconstruction algorithm as the AutoTrader path — the data
        shape is identical (TradeRecord + OrderResult). Separate classmethod
        for symmetry and future per-pipeline divergence.

        Args:
            trade_history: All TradeRecord from process_result.tick_loop_results
            order_history: All OrderResult from process_result.tick_loop_results
            run_dir: Scenario set log directory (e.g. logs/scenario_sets/<set>/<ts>/)

        Returns:
            EventStreamWriter ready to flush.
        """
        events = _build_events(trade_history, order_history)
        return cls(run_dir, events)

    def flush(self, filename: str = 'events.csv') -> Optional[Path]:
        """
        Sort events by timestamp and write the CSV file.

        Args:
            filename: Output filename inside run_dir. AutoTrader convention
                is 'events.csv'; sim convention is 'events_<scenario>.csv'
                (caller passes the scenario name).

        Returns:
            Path to the written CSV, or None if run_dir is None or no events.
        """
        if self._run_dir is None or not self._events:
            return None

        # Sort defensively: promote any naive timestamp to UTC so mixed
        # tz-aware / tz-naive datetimes don't break comparison. Project
        # convention is tz-aware everywhere (CLAUDE.md §9), but the sort
        # cannot afford to crash if a legacy path slips a naive one through.
        def _sort_key(event: TradeEvent) -> datetime:
            ts = event.ts
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            return ts

        sorted_events = sorted(self._events, key=_sort_key)
        out_path = self._run_dir / filename
        try:
            with open(out_path, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(EVENT_FIELDS)
                for event in sorted_events:
                    writer.writerow(_event_to_row(event))
        except Exception as e:
            print(f"Warning: Failed to write event-stream CSV {out_path}: {e}")
            return None

        return out_path


# ============================================
# Event reconstruction
# ============================================

def _build_events(
    trade_history: List[TradeRecord],
    order_history: List[OrderResult],
) -> List[TradeEvent]:
    """
    Reconstruct the event stream from terminal-state lists.

    Order-history walk emits SUBMIT / FILL / REJECT events. Trade-history
    walk emits POSITION_OPEN / POSITION_CLOSE events plus per-execution
    FILL events from entry_trades / exit_trades. The terminal state is
    enough — no inline emission required.

    Args:
        trade_history: Completed TradeRecord list
        order_history: All OrderResult states (PENDING + EXECUTED + REJECTED)

    Returns:
        Unsorted event list. Caller sorts at flush.
    """
    events: List[TradeEvent] = []

    # Order-history walk — ORDER_SUBMIT (opens only) + ORDER_REJECT. Close
    # submissions are emitted from trade_history below: 1:1 with TradeRecord
    # (each close = one algo decision). This avoids the dedup problem where
    # 3 partial closes on the same position_id would collapse to one
    # CLOSE_SUBMIT if we keyed on (order_id, action) alone.
    from collections import OrderedDict
    open_groups: 'OrderedDict[str, List[OrderResult]]' = OrderedDict()
    for order in order_history:
        # action is first-class field on OrderResult; default to OPEN for
        # legacy or constructor sites that haven't set it explicitly.
        action = order.action if order.action is not None else OrderAction.OPEN
        if action != OrderAction.OPEN:
            continue  # closes are handled by the trade_history walk
        open_groups.setdefault(order.order_id, []).append(order)

    for order_id, orders in open_groups.items():
        rejected = next((o for o in orders if o.is_rejected), None)
        if rejected:
            events.append(TradeEvent(
                ts=rejected.execution_time or datetime.now(timezone.utc),
                event_type=EventType.ORDER_REJECT,
                order_id=order_id,
                status=rejected.status.value if rejected.status else '',
                close_reason=rejected.rejection_reason.value if rejected.rejection_reason else '',
                notes=rejected.rejection_message or '',
            ))
            continue

        # Earliest valid timestamp across PENDING/EXECUTED stages. Sim opens
        # store PENDING with execution_time=None and only set it on EXECUTED;
        # live behaves similarly. Skip if no source available rather than
        # planting a wallclock-now that lands at session end.
        ts = next((o.execution_time for o in orders if o.execution_time), None)
        if ts is None:
            continue

        rep = orders[0]
        events.append(TradeEvent(
            ts=ts,
            event_type=EventType.ORDER_SUBMIT,
            order_id=order_id,
            position_id=rep.position_id or '',
            lots=rep.executed_lots,
            price=rep.executed_price,
            status=OrderStatus.PENDING.value,
        ))

    # Trade-history walk — POSITION_OPEN + FILL + POSITION_CLOSE events
    # with full per-execution detail from entry_trades / exit_trades.
    seen_position_opens = set()
    for trade in trade_history:
        # POSITION_OPEN once per position_id (shared across partial closes)
        if trade.position_id not in seen_position_opens:
            seen_position_opens.add(trade.position_id)

            # Emit FILL events for the original entry executions
            for bt in trade.entry_trades:
                events.append(_fill_event(bt, trade.position_id))

            events.append(TradeEvent(
                ts=trade.entry_time,
                event_type=EventType.POSITION_OPEN,
                order_id=trade.position_id,
                position_id=trade.position_id,
                direction=trade.direction,
                lots=_sum_volume(trade.entry_trades) or trade.lots,
                price=trade.entry_price,
                status='filled',
                notes='vwap' if len(trade.entry_trades) > 1 else '',
            ))

        # CLOSE_SUBMIT — one per TradeRecord (algo-decided close moment).
        # Slight negative offset so chronological sort places it before the
        # FILLs and POSITION_CLOSE that share the same exit_time in sim.
        events.append(TradeEvent(
            ts=trade.exit_time,
            event_type=EventType.CLOSE_SUBMIT,
            order_id=trade.position_id,
            position_id=trade.position_id,
            direction=trade.direction,
            lots=trade.lots,
            status=OrderStatus.PENDING.value,
            close_type=trade.close_type.value if isinstance(trade.close_type, CloseType) else str(trade.close_type),
        ))

        # Per-close FILL events + POSITION_CLOSE
        for bt in trade.exit_trades:
            events.append(_fill_event(bt, trade.position_id))

        events.append(TradeEvent(
            ts=trade.exit_time,
            event_type=EventType.POSITION_CLOSE,
            order_id=trade.position_id,
            position_id=trade.position_id,
            direction=trade.direction,
            lots=trade.lots,
            price=trade.exit_price,
            fee=trade.total_fees,
            fee_currency=trade.account_currency,
            status='closed',
            close_type=trade.close_type.value if isinstance(trade.close_type, CloseType) else str(trade.close_type),
            close_reason=trade.close_reason.value if trade.close_reason else '',
            notes=f'gross {trade.gross_pnl:+.2f} net {trade.net_pnl:+.2f}',
        ))

    return events


def _fill_event(bt: BrokerTrade, position_id: str) -> TradeEvent:
    """
    Build a FILL event row from one atomic BrokerTrade.

    bt.side is OrderSide (BUY/SELL) — populates the `side` column. The
    `direction` column stays empty on FILL rows (FIX-style: OrdSide vs
    PositionSide are distinct fields). POSITION_OPEN / POSITION_CLOSE rows
    carry `direction` (LONG/SHORT, position view) instead.
    """
    return TradeEvent(
        ts=bt.timestamp,
        event_type=EventType.FILL,
        order_id=bt.order_id,
        position_id=position_id,
        trade_id=bt.trade_id,
        broker_ref=bt.parent_broker_ref or '',
        side=bt.side,
        lots=bt.volume,
        price=bt.price,
        fee=bt.fee,
        fee_currency=bt.fee_currency,
        is_maker=bt.is_maker,
    )


def _sum_volume(trades: List[BrokerTrade]) -> float:
    """Total volume across an execution list. Empty list returns 0.0."""
    return sum(t.volume for t in trades) if trades else 0.0


def _event_to_row(event: TradeEvent) -> List[str]:
    """Serialize one TradeEvent to a CSV row matching EVENT_FIELDS order."""
    return [
        event.ts.isoformat() if event.ts else '',
        event.event_type.value,
        event.order_id,
        event.position_id,
        event.trade_id,
        event.broker_ref,
        event.direction.value if event.direction else '',
        event.side.value if event.side else '',
        f'{event.lots:.8f}' if event.lots is not None else '',
        f'{event.price:.5f}' if event.price is not None else '',
        f'{event.fee:.8f}' if event.fee is not None else '',
        event.fee_currency,
        event.status,
        event.close_type,
        event.close_reason,
        '' if event.is_maker is None else ('true' if event.is_maker else 'false'),
        event.notes,
    ]
