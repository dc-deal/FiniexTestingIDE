"""
FiniexTestingIDE - Cold-Start Adopter (#355 Phase 2)

On boot the executor's shadow state is empty while the venue still holds what an earlier
session left there. A bot that starts anyway trades beside its own open orders without seeing
them. This step rebuilds them — but only what is provably OURS.

**Ownership, not existence, decides what may be adopted.** A resting order carries the client
order id this bot minted (#473), so "mine" is a fact. A spot BALANCE carries no owner tag, so
"mine" would be a guess — balances are therefore not adopted here at all; what a bot may use is
declared capital, which is a different mechanism entirely.

Phase 1 built the classification this reuses, with the verdict inverted: mid-session a foreign
session key is a divergence, at boot it is a CANDIDATE.

The truth pull runs against the adapter directly rather than through the Reconciler. Adoption is
a boot step, not a reconcile cycle, and tying it to `reconciliation.enabled` would make it
disappear exactly where it is cheapest to exercise — a mock session.
"""

from typing import List, Optional, Set, Tuple

from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.persistence.cold_start_state_store import ColdStartStateStore
from python.framework.trading_env.live.live_trade_executor import LiveTradeExecutor
from python.framework.types.config_types.autotrader_defaults_config_types import ColdStartDefaults
from python.framework.types.live_types.reconciliation_types import BrokerOrder
from python.framework.types.trading_env_types.order_types import OrderType
from python.framework.utils.connection_ladder import run_with_ladder
from python.framework.utils.run_id_utils import parse_client_order_id

# Order types that actually REST at a venue. A MARKET order in the open list is in flight, not
# resting, and adopting one would put it into a world where nothing triggers it. STOP types are
# here because the venue can hold them even though this project's live submit path for them
# arrives with #209 — the guard should not have to be revisited then.
_RESTING_ORDER_TYPES = (OrderType.LIMIT, OrderType.STOP, OrderType.STOP_LIMIT)


class ColdStartAdopter:
    """
    Reconstructs this bot's resting orders from broker truth, once, before the tick loop.

    Args:
        executor: The live executor — adapter, session key, adoption surface, REST ladder
        store: The framework carry-over (the session keys of earlier runs)
        config: Cold-start defaults — enabled, adoption mode
        symbol: The traded symbol; one profile is one symbol, which is what lets an internal
            order id be rebuilt from a wire key that carries only the counter
        logger: Session logger — the channel that reaches the run outcome (§35)
        dry_run: True when no order of this session will reach the venue
        interactive: True when a human DECLARED they are watching this start (`--attended`)
            AND there is a terminal to answer into. A declaration, not an inference: a TTY
            proves nothing about whether anybody reads it
    """

    def __init__(
        self,
        executor: LiveTradeExecutor,
        store: ColdStartStateStore,
        config: ColdStartDefaults,
        symbol: str,
        logger: AbstractLogger,
        dry_run: bool = False,
        interactive: bool = False,
    ):
        self._executor = executor
        self._store = store
        self._config = config
        self._symbol = symbol
        self._logger = logger
        self._dry_run = dry_run
        self._interactive = interactive
        self._adopted_count: int = 0
        # Session halves of every key OF OUR SHAPE the venue reported, whether we could
        # attribute it or not. The carry-over uses this to protect a key that a resting order
        # still depends on from being evicted by newer sessions.
        self._venue_session_keys: Set[str] = set()

    def get_adopted_count(self) -> int:
        """
        How many resting orders were adopted.

        Returns:
            Count for the session summary; 0 when nothing was adopted
        """
        return self._adopted_count

    def get_venue_session_keys(self) -> Set[str]:
        """
        Session discriminators the venue reported on orders of our shape.

        Handed to the carry-over so it never evicts a key a resting order still depends on:
        eviction by recency alone would drop the key that owns an order still at the venue,
        and the successor would then read its own order as a stranger's.

        Returns:
            The session halves seen, attributable or not; empty before run()
        """
        return set(self._venue_session_keys)

    # ============================================
    # The boot step
    # ============================================

    def run(self) -> bool:
        """
        Pull broker truth, decide, adopt — or refuse to start.

        Returns:
            True to continue booting, False to abort the session before it trades
        """
        payload = self._store.load()
        known_keys = set(payload.session_keys)

        if self._dry_run:
            # A dry run cannot ask the venue anything: the adapter short-circuits its private
            # reads behind a sentinel and answers with an empty list. Reporting that as
            # "nothing of ours is resting" would be a statement about a venue we never
            # queried — and it would be made in exactly the mode an operator uses to rehearse.
            # So say what is true: this mode cannot see. Reading in a dry run is harmless and
            # free, and separating "do not WRITE" from "do not LOOK" is #304's job.
            self._logger.info(
                '🧬 Cold start (dry run): the venue was NOT queried — dry run suppresses the '
                'private reads, so nothing can be said about resting orders. Adoption is '
                'exercised by the cold-start suite and by a real run, not by a rehearsal.'
            )
            self._raise_counter_floor([], payload.highest_position_counter)
            return True

        broker_orders = self._pull_broker_orders()
        if broker_orders is None:
            self._logger.error(
                '❌ COLD START ABORTED — broker truth is unreachable, so the session cannot '
                'know what it already has resting at the venue. Starting blind is the one '
                'thing this step exists to prevent.'
            )
            return False

        ours, unattributable, foreign = self._split(broker_orders, known_keys)
        self._report_unattributable(unattributable)

        if not ours:
            self._logger.info(
                f'🧬 Cold start: nothing of ours resting at the broker '
                f'({len(foreign)} foreign order(s) left alone)'
            )
            self._raise_counter_floor([], payload.highest_position_counter)
            return True

        self._announce(ours, foreign)

        if not self._may_adopt(len(ours)):
            return False

        self._executor.adopt_resting_orders(ours)
        self._adopted_count = len(ours)
        self._raise_counter_floor(ours, payload.highest_position_counter)
        # The consequence is stated, not left to be discovered. An adopted order makes
        # `has_pending_orders()` true from the first tick, and the common algo gate
        # `if self.trading_api.has_pending_orders(): return` then does nothing at all for as
        # long as the order rests — which can be days. Nothing tells the algo WHY, so the
        # operator has to be able to read it here. Letting a decision logic answer for the
        # situation itself is the `on_cold_start` hook, its own issue.
        self._logger.warning(
            f'🧬 Cold start: {len(ours)} order(s) adopted, polling resumes. NOTE: '
            f'has_pending_orders() is true from the first tick — an algo that gates on it '
            f'will wait until these orders fill or are cancelled.'
        )
        return True

    # ============================================
    # Internals
    # ============================================

    def _pull_broker_orders(self) -> Optional[List[BrokerOrder]]:
        """
        Read the venue's open orders under the shared REST ladder (§43).

        Returns:
            The venue's resting orders, or None when the ladder gave up
        """
        adapter = self._executor.broker.adapter
        return run_with_ladder(adapter.get_broker_orders, self._executor.get_rest_ladder())

    def _split(
        self,
        broker_orders: List[BrokerOrder],
        known_keys: Set[str],
    ) -> Tuple[List[Tuple[str, BrokerOrder]], List[BrokerOrder], List[BrokerOrder]]:
        """
        Sort broker truth into three groups, because there are three answers, not two.

        An order is OURS when its client order id parses into a session discriminator this bot
        has actually SENT under — the carry-over's list. The shape alone is not enough: another
        client using the same format would otherwise be claimed, and claiming a stranger's
        order is the one mistake with an owner on the other side.

        But an order whose key has our SHAPE and whose session we cannot place is neither.
        Reporting it as foreign would state something we do not know, and it is the exact
        shape of a lost or evicted carry-over — so it gets its own group and its own report.

        An order for a DIFFERENT symbol is foreign here whatever its key says: the venue's
        open-order list is account-wide, this bot trades one symbol, and adopting a foreign
        symbol would build an id from OUR symbol and then crash the fill path, which requires
        the tick and the order to agree on the instrument.

        Args:
            broker_orders: Broker truth
            known_keys: Session discriminators this bot has used

        Returns:
            ((recovered order id, order) pairs, unattributable, foreign)
        """
        ours: List[Tuple[str, BrokerOrder]] = []
        unattributable: List[BrokerOrder] = []
        foreign: List[BrokerOrder] = []

        for order in broker_orders:
            parsed = parse_client_order_id(order.client_order_id)
            if parsed is None:
                foreign.append(order)
                continue

            self._venue_session_keys.add(parsed[0])

            if order.order_type not in _RESTING_ORDER_TYPES:
                # A MARKET order in the venue's open list is IN FLIGHT, not resting — it has
                # been accepted and not yet filled. Adopting it would put a market order into
                # the resting-order world, where nothing ever triggers it. It is also the one
                # case where the wire key is ambiguous: a close carries the key of the
                # POSITION it closes, so it is indistinguishable from that position's entry
                # order. Both reasons point the same way — report it, do not adopt it.
                self._logger.warning(
                    f'🧬 Cold start: order {order.broker_ref} carries our key '
                    f'({order.client_order_id}) but is a {order.order_type.value} order — '
                    f'in flight at the venue rather than resting, so it is not adopted. '
                    f'Check whether it filled.'
                )
                foreign.append(order)
                continue

            if order.symbol != self._symbol:
                self._logger.warning(
                    f'🧬 Cold start: a key of our shape ({order.client_order_id}) sits on a '
                    f'{order.symbol} order, but this bot trades {self._symbol} — left alone.'
                )
                foreign.append(order)
                continue

            if parsed[0] not in known_keys:
                unattributable.append(order)
                continue

            ours.append((f'pos_{self._symbol.lower()}_{parsed[1]}', order))

        return ours, unattributable, foreign

    def _report_unattributable(self, unattributable: List[BrokerOrder]) -> None:
        """
        Say out loud that an order may be ours and cannot be proven so.

        This is an ERROR rather than a note, and the reason is what it usually means: the
        carry-over that would identify the order is gone — lost, corrupt, or evicted — and a
        session that shrugs here goes on to trade beside its own untracked resting order while
        the log says "nothing of ours". It is not a refusal, because the order may genuinely
        belong to somebody else and refusing forever would leave the operator no way out.

        Args:
            unattributable: Orders of our key shape whose session we cannot place
        """
        for order in unattributable:
            self._logger.error(
                f'❌ Cold start: order {order.broker_ref} carries a client key of OUR shape '
                f'({order.client_order_id}) but a session this bot has no record of. It may be '
                f'ours from a session whose carry-over was lost or evicted, and it is NOT '
                f'adopted — check the account by hand before letting this run continue.'
            )

    def _announce(
        self,
        ours: List[Tuple[str, BrokerOrder]],
        foreign: List[BrokerOrder],
    ) -> None:
        """
        Say what was found, before anything is decided.

        Args:
            ours: The adoption candidates
            foreign: Orders belonging to somebody else
        """
        lines = [
            f'🧬 Cold start: {len(ours)} resting order(s) of ours at the broker'
            + (f', {len(foreign)} foreign' if foreign else ''),
        ]
        for order_id, order in ours:
            lines.append(
                f'   {order_id}  ({order.client_order_id})  {order.direction.value} '
                f'{order.lots} {order.symbol} @ {order.price}  ref={order.broker_ref}'
            )
        self._logger.info('\n'.join(lines))

    def _may_adopt(self, count: int) -> bool:
        """
        Apply the adoption policy — and refuse rather than block when nobody can answer.

        `operator_confirm` without a DECLARED human is the unattended-boot trap: a bot that
        waits for a confirmation at 03:00 has simply stopped. It refuses instead, and stays
        flat, which is a state an operator can find in the morning. Not starting while our own
        orders rest at the venue is deliberate: a bot that does not account for its own open
        orders must not trade beside them.

        `interactive` is a DECLARATION (`--attended`), not an inference. A terminal proves
        nothing about whether anyone is reading it — this project's own container sets
        `tty: true` — so `isatty()` alone would have re-introduced the very hang this guards
        against, in the very environment the bot runs in.

        Args:
            count: How many orders are waiting to be adopted

        Returns:
            True when adoption may proceed
        """
        if self._config.adoption_mode == 'auto':
            self._logger.warning(
                f'🧬 Cold start: adopting {count} order(s) automatically '
                f"(adoption_mode='auto'). No operator confirmed this."
            )
            return True

        if not self._interactive:
            self._logger.error(
                f'❌ COLD START ABORTED — {count} resting order(s) of ours need confirmation '
                f"and nobody declared themselves present (adoption_mode='operator_confirm'). "
                f'The session stays flat and trades nothing. Either start it with --attended '
                f"from a terminal, set adoption_mode='auto' for unattended running, or cancel "
                f'the orders at the broker.'
            )
            return False

        # The prompt says what adoption COSTS, not only what it does. `close_all_remaining_orders`
        # cancels every active order and closes every open position at session end — a net from
        # before cold start existed — so confirming here and hitting any later abort ends with
        # the orders cancelled at the venue. An operator who is not told that would read
        # "adopt and resume" as "leave them be".
        answer = input(
            f'  ▸ Adopt {count} resting order(s) and resume?\n'
            f'    Note: this session cancels every active order when it ENDS (normally or by '
            f'abort), so adopting them also puts them under that cleanup. [y/N] '
        ).strip().lower()
        if answer not in ('y', 'yes'):
            self._logger.error(
                '❌ COLD START ABORTED — the operator declined adoption. The session stays '
                'flat; the orders are untouched at the broker.'
            )
            return False
        return True

    def _raise_counter_floor(
        self,
        ours: List[Tuple[str, BrokerOrder]],
        carried_floor: int,
    ) -> None:
        """
        Lift the position counter above everything this bot has already minted.

        Two sources, and both are needed: adoption recovers the counters of orders still
        resting, the carry-over recovers the ones whose orders are already gone. Without the
        second, a successor re-mints ids its predecessor used, and a diagnostics reader
        joining run records finds two different positions under one name.

        Args:
            ours: The adopted (order id, broker order) pairs
            carried_floor: The high-water mark from the carry-over
        """
        counters = [carried_floor]
        for order_id, _ in ours:
            tail = order_id.rsplit('_', 1)[-1]
            if tail.isdigit():
                counters.append(int(tail))

        floor = max(counters)
        if floor > 0:
            self._executor.portfolio.raise_position_counter_floor(floor)
            self._logger.info(
                f'🧬 Cold start: position counter lifted to {floor} so no id is minted twice'
            )
