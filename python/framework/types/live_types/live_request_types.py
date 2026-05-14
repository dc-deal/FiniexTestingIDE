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
    SubmitJob       open or close submission
    EditJob         modify a pending limit order (#318)
    CancelJob       cancel a pending limit order (#318)

Response types (inbox, worker → main):
    SubmitResponse  result of a SubmitJob
    EditResponse    result of an EditJob (#318)
    CancelResponse  result of a CancelJob (#318)
"""

from dataclasses import dataclass
from typing import Any, Dict

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
