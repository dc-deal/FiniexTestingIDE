"""
FiniexTestingIDE - Dry-Run Order Simulator

Shared utility for live-capable adapters operating in dry-run mode.
Provides a proper PENDING → FILLED lifecycle instead of instant-fill,
so the same pending-order tracking, polling, and reconciliation paths
run in dry-run as in live.

Lifecycle:
    submit  → PENDING with synthetic DRYRUN-NNNNNN ref
    query   → advances per-order poll counter; PENDING while the counter
              is unspent OR the market has not reached the order's price,
              FILLED once both are satisfied
    cancel  → CANCELLED, removed from state (idempotent)
    modify  → in-place amend, same ref (mimics Kraken AmendOrder)

The simulator is framework-agnostic — adapters compose it from their
Tier-3 transport layers (_do_request_*) when self._dry_run is True.
Real-mode transport remains untouched.

IT PLAYS THE VENUE, so it has to answer the venue's question (#505). It used to answer a
different one: every order flipped to FILLED after two polls and a MARKET order filled at
`0.0`. With `poll_interval_ms = 5000` that meant every resting order "filled" about ten
seconds after placement at a price nobody chose — and `dry_run: true` is the shipped default
for kraken_spot, so the first rehearsal of any resting-order feature reported a stop that had
fired at zero. A confident wrong answer, in the mode meant to make a feature safe to try.

Now an order fills when the MARKET reaches its price, at the price the MARKET is then at,
using the same predicate and the same book side the backtest uses
(`utils/trading_math/price_trigger.py`). Parity is the whole point: a rehearsal that disagrees
with the backtest about WHEN or AT WHAT an order fills predicts nothing.

    MARKET      the poll counter is spent          → the quote AT THAT POLL
    LIMIT       the market reached the limit       → the limit
    STOP        the market crossed the trigger     → the QUOTE, not the trigger
    STOP_LIMIT  the trigger fires, then the limit  → the limit

The two prices that are NOT obvious were both got wrong first, and the simulation is the
authority on both. A MARKET order is priced when it ARRIVES, not when it was sent — the
simulation stores `entry_price = 0` for a market order and fills from the tick current after
the latency (`order_latency_simulator.py` / `trade_simulator.py`), so pricing at submit made
the rehearsal show zero round-trip slippage by construction. And a triggered STOP fills at the
market, in the simulation's own words `# STOP triggered → fill at current market price`;
filling it at its trigger flattered every stop by the distance the price had moved through it.

The poll counter keeps its old meaning and gains a boundary: it models the round trip to the
venue, and it is now a condition ALONGSIDE the price rather than instead of it.

Where the facts run out — no quote to compare against, an order type nothing here models, or a
reference this simulator never issued — it REFUSES: the order stays PENDING and the response
says why. A refusal in a rehearsal is information; a fabricated fill is worse than no
rehearsal at all.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, Optional, Set, Tuple

from python.framework.types.live_types.live_execution_types import (
    BrokerOrderStatus,
    BrokerResponse,
)
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.trading_env_types.order_types import OrderDirection, OrderType
from python.framework.utils.trading_math.price_trigger import (
    is_limit_reached,
    is_stop_reached,
    taken_price,
)


@dataclass
class _DryRunOrderState:
    """
    Per-order state tracked by the simulator.

    The two prices are SEPARATE fields on purpose. One overloaded `price` meaning "limit or
    trigger, depending on the type" is what let an amend of a STOP_LIMIT's limit overwrite its
    trigger — Kraken's own AmendOrder keeps them apart (`trigger_price` / `limit_price`), and
    so does this.
    """
    lots: float
    direction: OrderDirection
    order_type: OrderType
    remaining_polls: int
    limit_price: Optional[float] = None
    trigger_price: Optional[float] = None
    # STOP_LIMIT only: the trigger has fired and the order is now a resting limit.
    triggered: bool = False


class DryRunOrderSimulator:
    """
    Stateful dry-run lifecycle simulator.

    One instance per adapter — counter and order state are isolated so
    parallel adapters do not share refs.
    """

    def __init__(self, polls_until_fill: int = 2):
        """
        Args:
            polls_until_fill: How many query() calls a PENDING order must
                              receive before it may fill. Default 2
                              matches the typical tick-loop cadence
                              (one tick → poll → still pending → next
                              tick → poll → fill). It models the round
                              trip to the venue, so it applies to a
                              resting order that is immediately reachable
                              too — the order still had to get there.
        """
        self._counter: int = 0
        self._polls_until_fill = polls_until_fill
        self._orders: Dict[str, _DryRunOrderState] = {}
        # References this simulator has already resolved. A re-query after a fill has to keep
        # answering FILLED (callers rely on it), but a reference we NEVER issued is a
        # different thing entirely — after a restart an adopted DRYRUN-* ref would have been
        # answered FILLED with no price and booked (#355 adopts resting orders at boot).
        self._retired: Set[str] = set()

    def submit(
        self,
        lots: float,
        timestamp: datetime,
        direction: OrderDirection,
        order_type: OrderType,
        limit_price: Optional[float] = None,
        trigger_price: Optional[float] = None,
    ) -> BrokerResponse:
        """
        Register a new dry-run order. Returns PENDING with a synthetic ref.

        No quote is taken here. Every price this order can fill at is decided at POLL time,
        because that is when a venue prices it — see the module docstring.

        Args:
            lots: Order size (preserved for the eventual fill)
            timestamp: Response timestamp (UTC)
            direction: LONG for a buy, SHORT for a sell — decides which side of the quote
                a fill uses and which way a trigger points
            order_type: Which fill rule applies
            limit_price: The limit of a LIMIT, or the second leg of a STOP_LIMIT
            trigger_price: The trigger of a STOP or a STOP_LIMIT

        Returns:
            BrokerResponse(status=PENDING, broker_ref=DRYRUN-NNNNNN)
        """
        self._counter += 1
        broker_ref = f'DRYRUN-{self._counter:06d}'
        self._orders[broker_ref] = _DryRunOrderState(
            lots=lots,
            direction=direction,
            order_type=order_type,
            remaining_polls=self._polls_until_fill,
            limit_price=limit_price,
            trigger_price=trigger_price,
        )
        return BrokerResponse(
            broker_ref=broker_ref,
            status=BrokerOrderStatus.PENDING,
            timestamp=timestamp,
        )

    def query(
        self,
        broker_ref: str,
        timestamp: datetime,
        market: Optional[TickData] = None,
    ) -> BrokerResponse:
        """
        Poll a dry-run order and decide whether the market has filled it.

        Unknown broker_refs (e.g. already filled and removed, or one this
        simulator never issued) are reported as FILLED — matches the
        legacy "DRYRUN-* always FILLED on query" behavior so callers that
        re-query after a fill do not see false PENDING.

        The quote is TAKEN ON TRUST as belonging to this order's symbol. The simulation
        checks that (`pending.symbol != self._current_tick.symbol`) because it runs many
        symbols at once; a live AutoTrader profile is one symbol by construction (§31), so
        there is nothing here to confuse. Should a session ever trade two, this order state
        has to carry its symbol and compare — it cannot today, because it does not know it.

        Args:
            broker_ref: Synthetic DRYRUN-* reference
            timestamp: Response timestamp (UTC)
            market: The current quote, or None when none is available

        Returns:
            BrokerResponse — FILLED with a real price, PENDING while the order waits, or
            PENDING with `undecided_reason` where the facts do not allow an answer
        """
        order = self._orders.get(broker_ref)
        if order is None:
            if broker_ref in self._retired:
                # We filled this one and dropped it. A re-query must not read as a false
                # PENDING; no booking is derived from this answer, the fill already was.
                return BrokerResponse(
                    broker_ref=broker_ref,
                    status=BrokerOrderStatus.FILLED,
                    timestamp=timestamp,
                )
            return BrokerResponse(
                broker_ref=broker_ref,
                status=BrokerOrderStatus.PENDING,
                undecided_reason=(
                    f'{broker_ref} was never issued by this simulator — nothing here knows '
                    f'what happened to it, and answering FILLED would invent a fill'),
                timestamp=timestamp,
            )

        order.remaining_polls -= 1

        fill_price, refusal = self._decide(order, market)
        if refusal is not None:
            return BrokerResponse(
                broker_ref=broker_ref,
                status=BrokerOrderStatus.PENDING,
                undecided_reason=refusal,
                timestamp=timestamp,
            )
        if fill_price is None or order.remaining_polls > 0:
            return BrokerResponse(
                broker_ref=broker_ref,
                status=BrokerOrderStatus.PENDING,
                timestamp=timestamp,
            )

        filled_lots = order.lots
        self._orders.pop(broker_ref, None)
        self._retired.add(broker_ref)
        return BrokerResponse(
            broker_ref=broker_ref,
            status=BrokerOrderStatus.FILLED,
            fill_price=fill_price,
            filled_lots=filled_lots,
            timestamp=timestamp,
        )

    def cancel(self, broker_ref: str, timestamp: datetime) -> BrokerResponse:
        """
        Cancel a dry-run order. Idempotent — removes from state if
        present, always returns CANCELLED.

        Args:
            broker_ref: Synthetic DRYRUN-* reference
            timestamp: Response timestamp (UTC)

        Returns:
            BrokerResponse(status=CANCELLED)
        """
        self._orders.pop(broker_ref, None)
        return BrokerResponse(
            broker_ref=broker_ref,
            status=BrokerOrderStatus.CANCELLED,
            timestamp=timestamp,
        )

    def modify(
        self,
        broker_ref: str,
        timestamp: datetime,
        new_limit_price: Optional[float] = None,
        new_trigger_price: Optional[float] = None,
    ) -> BrokerResponse:
        """
        Modify a dry-run order in-place. Mirrors Kraken AmendOrder
        semantics — the order keeps the SAME ref; only the prices given are
        applied (per-order state lots/remaining_polls is preserved).

        TWO legs, kept apart, because Kraken keeps them apart and because one shared field
        got them crossed: amending a STOP_LIMIT's limit used to overwrite its TRIGGER, and an
        amended trigger never arrived at all — so a rehearsal went on watching the old level
        while our books showed the new one.

        Args:
            broker_ref: Synthetic DRYRUN-* reference (unchanged by the amend)
            timestamp: Response timestamp (UTC)
            new_limit_price: New limit price (None = keep current)
            new_trigger_price: New trigger price of a triggered type (None = keep current)

        Returns:
            BrokerResponse(status=PENDING, broker_ref unchanged)
        """
        order = self._orders.get(broker_ref)
        if order is not None:
            if new_limit_price is not None:
                order.limit_price = new_limit_price
            if new_trigger_price is not None:
                order.trigger_price = new_trigger_price
                # A moved trigger is a trigger that has not fired yet.
                order.triggered = False
        return BrokerResponse(
            broker_ref=broker_ref,
            status=BrokerOrderStatus.PENDING,
            timestamp=timestamp,
        )

    def _decide(
        self,
        order: _DryRunOrderState,
        market: Optional[TickData],
    ) -> Tuple[Optional[float], Optional[str]]:
        """
        What this poll can say about the order.

        Args:
            order: The tracked order state
            market: The current quote, or None

        Returns:
            (fill_price, refusal) — a price when the market has filled it, (None, None)
            while it legitimately waits, and (None, reason) where no answer is possible
        """
        if order.order_type == OrderType.MARKET:
            if market is None:
                return None, 'no quote to price a MARKET order at'
            return taken_price(order.direction, market.bid, market.ask), None

        if order.order_type == OrderType.LIMIT:
            return self._decide_limit(order.direction, order.limit_price, market)

        if order.order_type == OrderType.STOP:
            return self._decide_stop(order.direction, order.trigger_price, market)

        if order.order_type == OrderType.STOP_LIMIT:
            if not order.triggered:
                fired, refusal = self._decide_stop(
                    order.direction, order.trigger_price, market)
                if refusal is not None or fired is None:
                    return None, refusal
                order.triggered = True
            return self._decide_limit(order.direction, order.limit_price, market)

        return None, (f'{order.order_type} has no venue-side model in this project, so a '
                      'dry-run fill would be an invention rather than a rehearsal')

    @staticmethod
    def _decide_limit(
        direction: OrderDirection,
        limit_price: Optional[float],
        market: Optional[TickData],
    ) -> Tuple[Optional[float], Optional[str]]:
        """
        Has a limit order been reached?

        A limit fills AT its own price: that is what a limit order is for, and it is what the
        simulation books (`fill_price = pending.entry_price` on its limit path).

        Args:
            direction: Order direction
            limit_price: The limit
            market: The current quote, or None

        Returns:
            (fill_price, refusal) as described on _decide
        """
        if limit_price is None:
            return None, 'a LIMIT order arrived without a limit price'
        if market is None:
            return None, 'no quote to compare the limit against'
        if not is_limit_reached(direction, limit_price, market.bid, market.ask):
            return None, None
        return limit_price, None

    @staticmethod
    def _decide_stop(
        direction: OrderDirection,
        trigger_price: Optional[float],
        market: Optional[TickData],
    ) -> Tuple[Optional[float], Optional[str]]:
        """
        Has a stop trigger been crossed, and at what does it then fill?

        At the MARKET, not at the trigger. A stop is an instruction to trade once a level is
        passed, and by the time it is passed the price is beyond it — the simulation says so
        in its own comment, `# STOP triggered → fill at current market price`. Filling at the
        trigger flatters every stop by exactly the distance the market moved through it, which
        on a gap is the whole gap.

        Args:
            direction: Order direction
            trigger_price: The trigger
            market: The current quote, or None

        Returns:
            (fill_price, refusal) as described on _decide
        """
        if trigger_price is None:
            return None, 'a STOP order arrived without a trigger price'
        if market is None:
            return None, 'no quote to compare the trigger against'
        if not is_stop_reached(direction, trigger_price, market.bid, market.ask):
            return None, None
        return taken_price(direction, market.bid, market.ask), None
