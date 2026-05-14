"""
FiniexTestingIDE - Live Request Types
Job/Response dataclasses for the LiveRequestProcessor worker queue.

The worker thread consumes Job objects from _http_outbox, performs the
broker-side transport (build → request → parse), and pushes Response
objects into _http_inbox. drain_inbox() on the main thread consumes the
responses and applies portfolio mutations + listener notifications.

This isolates broker I/O from the main tick loop. The main thread never
performs HTTP / RPC / terminal-bridge calls — those live exclusively in
the worker. Cross-thread state transfer happens only via the two queues.

Job types (outbox, main → worker):
    SubmitJob              open or close submission
    EditJob                modify a pending order (#318)
    CancelJob              cancel a pending order (#318)
    PositionModifyJob      modify open-position SL/TP (#318, gated by
                           OrderCapabilities.native_position_sl_tp)

Response types (inbox, worker → main):
    SubmitResponse         result of a SubmitJob
    EditResponse           result of an EditJob (#318)
    CancelResponse         result of a CancelJob (#318)
    PositionModifyResponse result of a PositionModifyJob (#318)
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional

from python.framework.trading_env.adapters.abstract_adapter import AbstractAdapter
from python.framework.types.live_types.live_execution_types import BrokerResponse
from python.framework.types.trading_env_types.latency_simulator_types import PendingOrderAction
from python.framework.types.trading_env_types.order_types import OrderType


@dataclass
class SubmitJob:
    """
    Submit-order job carried via _http_outbox to the worker thread.

    The worker uses the adapter's Tier-3 layers to perform the broker call:
        adapter._do_request_submit(payload) → raw
        adapter._parse_submit_response(raw, timestamp) → BrokerResponse

    Args:
        order_id: Internal order identifier (links the job back to the
                  stored PendingOrder on response)
        action: OPEN or CLOSE — determines which fill callback the
                main thread invokes when the response arrives
        order_type: MARKET or LIMIT — controls drain_inbox routing.
                    MARKET pendings live in the processor; LIMIT pendings
                    live in AbstractTradeExecutor._active_limit_orders
                    (Hybrid pattern). The dispatcher delegates LIMIT
                    responses to the executor via a separate hook.
        payload: Pre-built broker payload (from adapter._build_submit_payload)
        adapter: Live-capable adapter (used by the worker for the
                 _do_request_submit / _parse_submit_response calls)
    """
    order_id: str
    action: PendingOrderAction
    order_type: OrderType
    payload: Dict[str, Any]
    adapter: AbstractAdapter


@dataclass
class SubmitResponse:
    """
    Result of a SubmitJob carried via _http_inbox to the main thread.

    drain_inbox() on the main thread consumes this and dispatches based
    on order_type:
        - MARKET: handled inside the processor (mark_filled / _fill_open
                  hook for FILLED, mark_rejected / rejection hook for
                  REJECTED, broker_ref set for PENDING)
        - LIMIT:  forwarded to the executor via _limit_response_hook so
                  the executor can update its _active_limit_orders list

    Args:
        order_id: Internal order identifier (matches the SubmitJob's order_id)
        action: OPEN or CLOSE (matches the SubmitJob's action)
        order_type: MARKET or LIMIT (matches the SubmitJob's order_type)
        broker_response: The parsed BrokerResponse from the adapter
    """
    order_id: str
    action: PendingOrderAction
    order_type: OrderType
    broker_response: BrokerResponse


# ============================================
# #318 — Async Modify / Cancel / Position-Modify
# ============================================
# These job/response pairs travel through the same _http_outbox / _http_inbox
# as SubmitJob/SubmitResponse. The worker dispatches them via the adapter's
# corresponding Tier-3 triples (_build_modify_payload / _do_request_modify /
# _parse_modify_response, etc.). drain_inbox handles each response on the
# main thread — applying the modification to the local shadow state and
# clearing the PendingOrder.in_flight_operation flag.


@dataclass
class EditJob:
    """
    Modify-order job carried via _http_outbox to the worker thread.

    The worker uses the adapter's Tier-3 modify-layer:
        adapter._build_modify_payload(broker_ref, symbol, new_price, sl, tp)
        adapter._do_request_modify(payload) → raw
        adapter._parse_modify_response(raw, broker_ref, timestamp) → BrokerResponse

    Args:
        order_id: Internal order identifier (links back to the PendingOrder
                  with in_flight_operation == PENDING_MODIFY)
        broker_ref: Current broker order reference at dispatch time. Some
                    brokers (Kraken EditOrder) return a NEW ref in the
                    response — the drain handler swaps refs in that case.
        symbol: Trading symbol (some brokers need this on modify, e.g. Kraken)
        new_price: New limit / stop_limit price (None = no change)
        new_stop_loss: New SL (None = no change in this batch — UNSET-to-None
                       translation happens at the executor boundary)
        new_take_profit: New TP (analog)
        adapter: Live-capable adapter
    """
    order_id: str
    broker_ref: str
    symbol: str
    new_price: Optional[float]
    new_stop_loss: Optional[float]
    new_take_profit: Optional[float]
    adapter: AbstractAdapter


@dataclass
class EditResponse:
    """
    Result of an EditJob carried via _http_inbox to the main thread.

    drain_inbox applies the provisional ModificationRequest to the
    PendingOrder on success, discards it on rejection, and clears the
    in_flight_operation flag in both cases.

    Args:
        order_id: Internal order identifier (matches EditJob.order_id)
        broker_response: Parsed BrokerResponse from the adapter. On success
                         the broker_ref field may differ from EditJob.broker_ref
                         (Kraken EditOrder semantic — caller must swap refs
                         via processor.update_broker_ref)
    """
    order_id: str
    broker_response: BrokerResponse


@dataclass
class CancelJob:
    """
    Cancel-order job carried via _http_outbox to the worker thread.

    Args:
        order_id: Internal order identifier (links back to the PendingOrder
                  with in_flight_operation == PENDING_CANCEL)
        broker_ref: Current broker order reference at dispatch time
        adapter: Live-capable adapter
    """
    order_id: str
    broker_ref: str
    adapter: AbstractAdapter


@dataclass
class CancelResponse:
    """
    Result of a CancelJob carried via _http_inbox to the main thread.

    drain_inbox removes the PendingOrder from local storage on successful
    cancellation. On rejection (e.g. cancel-during-fill race — broker
    reports the order already filled), the in_flight_operation flag is
    cleared and the next poll cycle picks up the real terminal state.

    Args:
        order_id: Internal order identifier (matches CancelJob.order_id)
        broker_response: Parsed BrokerResponse from the adapter
    """
    order_id: str
    broker_response: BrokerResponse


@dataclass
class PositionModifyJob:
    """
    Modify-position job — used when adapter declares
    OrderCapabilities.native_position_sl_tp = True (e.g. MT5, where SL/TP
    are server-side attached to the open position). For adapters without
    native attached SL/TP (e.g. Kraken Spot), the executor falls back to
    instant portfolio.modify_position and never enqueues this job.

    The worker uses a separate Tier-3 triple
        adapter._build_position_modify_payload(...)
        adapter._do_request_position_modify(payload) → raw
        adapter._parse_position_modify_response(raw, position_id, timestamp)
    OR folds it into the existing modify layer with a target discriminator
    (decision deferred to #209 implementation per ISSUE_209).

    Args:
        position_id: Position identifier
        symbol: Trading symbol (some brokers need this on position modify)
        new_stop_loss: New SL price (None = clear, absent = no change handled
                       at executor boundary)
        new_take_profit: New TP price (analog)
        adapter: Live-capable adapter with native_position_sl_tp = True
    """
    position_id: str
    symbol: str
    new_stop_loss: Optional[float]
    new_take_profit: Optional[float]
    adapter: AbstractAdapter


@dataclass
class PositionModifyResponse:
    """
    Result of a PositionModifyJob.

    drain_inbox applies the SL/TP change to portfolio.positions[position_id]
    on success, leaves it unchanged on rejection. In both cases the
    in_flight_operation tracking on the position is cleared (sim uses a
    _pending_position_modifications dict, live uses an analogous tracker
    on the processor or executor).

    Args:
        position_id: Position identifier (matches PositionModifyJob.position_id)
        broker_response: Parsed BrokerResponse from the adapter
    """
    position_id: str
    broker_response: BrokerResponse
