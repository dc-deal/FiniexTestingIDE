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

from typing import Dict, List, Optional, Set, Tuple

from python.framework.decision_logic.abstract_decision_logic import AbstractDecisionLogic
from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.persistence.cold_start_state_store import ColdStartStateStore
from python.framework.trading_env.live.live_trade_executor import LiveTradeExecutor
from python.framework.types.autotrader_types.cold_start_types import (
    AdoptedOrder,
    ColdStartSituation,
    ColdStartVerdict,
    SkippedOrder,
    SkipReason,
)
from python.framework.types.config_types.autotrader_defaults_config_types import ColdStartDefaults
from python.framework.types.live_types.reconciliation_types import BrokerOrder
from python.framework.types.persistence_types import PositionCarryOver
from python.framework.types.trading_env_types.order_types import OrderType
from python.framework.utils.broker_asset_utils import normalize_broker_asset
from python.framework.utils.connection_ladder import run_with_ladder
from python.framework.utils.run_id_utils import parse_client_order_id

# Order types that actually REST at a venue. A MARKET order in the open list is in flight, not
# resting, and adopting one would put it into a world where nothing triggers it. STOP types are
# here because the venue can hold them even though this project's live submit path for them
# arrives with #209 — the guard should not have to be revisited then.
_RESTING_ORDER_TYPES = (OrderType.LIMIT, OrderType.STOP, OrderType.STOP_LIMIT)

# Below this, a difference between the book and the venue's balance is float noise rather
# than a fact. One satoshi is the smallest unit any supported venue settles in, so a real
# divergence is always larger and a computed one is always smaller.
_BOOK_DUST = 1e-8

# Skip reasons whose order carries a key of OUR shape, so its counter came from a session of
# THIS bot. A foreign symbol is excluded on purpose: position ids are numbered per bot.
_OUR_SHAPE_REASONS = (SkipReason.UNKNOWN_SESSION, SkipReason.IN_FLIGHT)


def _counter_of(position_id: str) -> int:
    """
    Read the counter out of an internal position id.

    Args:
        position_id: An id of the form pos_<symbol>_<counter>

    Returns:
        The counter, or 0 when the tail is not a number
    """
    tail = position_id.rsplit('_', 1)[-1]
    return int(tail) if tail.isdigit() else 0


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
        decision_logic: Asked to answer for the situation (#493). None means nobody is asked,
            which is how the framework alone decides
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
        decision_logic: Optional[AbstractDecisionLogic] = None,
        session_end_orders: str = 'cancel',
    ):
        self._executor = executor
        self._store = store
        self._config = config
        self._symbol = symbol
        self._logger = logger
        self._dry_run = dry_run
        self._interactive = interactive
        self._decision_logic = decision_logic
        # #492: what this session will do with these very orders when it ends. The prompt
        # below is the one interactive, real-money decision the boot asks, so it has to
        # state the policy that will actually run rather than a behaviour that was removed.
        self._session_end_orders = session_end_orders
        self._situation: Optional[ColdStartSituation] = None
        self._verdict: Optional[ColdStartVerdict] = None
        self._restored_count: int = 0
        # Session halves of every key OF OUR SHAPE the venue reported, whether we could
        # attribute it or not. The carry-over uses this to protect a key that a resting order
        # still depends on from being evicted by newer sessions.
        self._venue_session_keys: Set[str] = set()

    def get_situation(self) -> Optional[ColdStartSituation]:
        """
        The boot situation as it was handed to the decision logic.

        Returns:
            The situation, or None for a dry run / an unreachable venue / before run()
        """
        return self._situation

    def get_verdict(self) -> Optional[ColdStartVerdict]:
        """
        What the decision logic answered about the boot situation.

        Returns:
            The verdict, or None when nobody was asked or the answer was malformed
        """
        return self._verdict

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
                'private reads, so nothing can be said about resting orders. The position '
                'book is not restored either: a rehearsal that closes remembered REAL '
                'positions with orders that never leave the process would report a book it '
                'does not have. Both paths are exercised by the cold-start suite and by a '
                'real run, not by a rehearsal.'
            )
            # No skipped orders and no book: a dry run queried nothing and restored nothing,
            # so the carry-over's own high-water mark is all there is to go on.
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

        # What the note offers, and whether this session may take it. Nothing is APPLIED in
        # this whole stretch: a boot that goes on to refuse must leave the executor exactly as
        # it found it, or the shutdown cleanup runs over positions the session never accepted
        # — before the first tick, which is where that cleanup dereferences the tick it does
        # not have yet.
        book = self._restorable_book(payload.open_positions)

        # Only asked when there is a book to hold against it. The ladder may be configured
        # to ABORT on a give-up (§43), so an unnecessary read is not merely a wasted REST
        # call — it is a way to end a boot over a number nobody needed.
        shortfall = 0.0
        if book:
            balances = self._pull_broker_balances()
            if balances is None:
                self._logger.error(
                    '❌ Cold start: the venue\'s balances could not be read, so the position '
                    'book was NOT held against them. The orders side was readable, so the '
                    'session may start — but nothing has confirmed that the account still '
                    'covers the book.'
                )
            else:
                shortfall = self._cross_check_book(book, balances)

        ours, skipped = self._split(broker_orders, known_keys)
        self._report_unattributable(skipped)

        self._situation = self._build_situation(ours, skipped, book, shortfall)
        verdict = self._consult_algo(self._situation)
        self._verdict = verdict

        if ours:
            self._announce(ours, skipped)
            if not self._may_adopt(ours, verdict):
                return False
        else:
            foreign = [o for o in skipped if o.reason == SkipReason.FOREIGN_KEY]
            self._logger.info(
                f'🧬 Cold start: nothing of ours resting at the broker '
                f'({len(foreign)} foreign order(s) left alone)'
            )

        # === From here the session is allowed to start, so state may change ===
        self._situation.applied = True
        self._restore_position_book(book)
        if ours:
            self._executor.adopt_resting_orders(ours)
            # The consequence is stated, not left to be discovered. An adopted order makes
            # `has_pending_orders()` true from the first tick, and the common algo gate
            # `if self.trading_api.has_pending_orders(): return` then does nothing at all for
            # as long as the order rests — which can be days. The algo can now be told
            # (#493), but the operator still has to be able to read it here.
            self._logger.warning(
                f'🧬 Cold start: {len(ours)} order(s) adopted, polling resumes. NOTE: '
                f'has_pending_orders() is true from the first tick — an algo that gates on it '
                f'will wait until these orders fill or are cancelled.'
            )
        self._raise_counter_floor(ours, payload.highest_position_counter, skipped, book)
        return True

    # ============================================
    # The algo's say (#493)
    # ============================================

    def _build_situation(
        self,
        ours: List[Tuple[str, BrokerOrder]],
        skipped: List[SkippedOrder],
        book: List[PositionCarryOver],
        shortfall: float,
    ) -> ColdStartSituation:
        """
        Assemble the read-only picture the decision logic gets to see.

        Complete on purpose — the whole account-wide list, including other symbols, because
        they bind capital even where this bot does not trade them. A bot may be able to
        account for an order it did NOT get, and it cannot do that from a count.

        Args:
            ours: The adoption candidates as (recovered order id, broker order) pairs
            skipped: Everything left alone, each with its reason
            book: The notes this session will restore — never the ones it declined
            shortfall: How much the restored book claims beyond what the account holds

        Returns:
            The situation, ready to hand over
        """
        return ColdStartSituation(
            symbol=self._symbol,
            adopted=[
                AdoptedOrder(
                    order_id=order_id,
                    client_order_id=order.client_order_id,
                    broker_ref=order.broker_ref,
                    symbol=order.symbol,
                    direction=order.direction,
                    order_type=order.order_type,
                    lots=order.lots,
                    filled_lots=order.filled_lots,
                    price=order.price,
                )
                for order_id, order in ours
            ],
            skipped=list(skipped),
            # Copies: these very records are handed to the portfolio afterwards, and a
            # situation documented as read-only must not be a handle on the state that is
            # about to be applied.
            restored_positions=[record.model_copy(deep=True) for record in book],
            carry_over_present=self._store.get_saved_at() is not None,
            carry_over_saved_at=self._store.get_saved_at(),
            adoption_mode=self._config.adoption_mode,
            attended=self._interactive,
            book_shortfall=shortfall,
        )

    def _consult_algo(self, situation: ColdStartSituation) -> Optional[ColdStartVerdict]:
        """
        Ask the decision logic to answer for the situation — and record what it said.

        Asked whenever the boot found ANYTHING — an order at the venue, a position read back
        from the carry-over, or a book the account no longer covers — and not only where a
        refusal is pending:
        under `adoption_mode='auto'` the framework never refuses, and that is exactly the
        mode an unattended thirty-day run uses — so a hook consulted only at the refusal
        would stay silent in the case it was built for. The verdict is only USED at the
        refusal (it may loosen, never tighten), and it is recorded either way, because the
        situation must not become invisible through a yes (#493).

        Args:
            situation: The picture handed over

        Returns:
            The verdict, or None when there was nothing to ask about
        """
        if self._decision_logic is None:
            return None
        if situation.is_empty():
            return None

        verdict = self._decision_logic.on_cold_start(situation)
        if verdict is None or not isinstance(verdict, ColdStartVerdict):
            # A hook that answers with something else is a defect in the algo, not a licence
            # to guess. Treated as "not accounted for" and said out loud.
            self._logger.error(
                f'❌ Cold start: {type(self._decision_logic).__name__}.on_cold_start() '
                f'returned {type(verdict).__name__} instead of a ColdStartVerdict — read as '
                f'"not accounted for".'
            )
            return None

        self._logger.info(
            f'🧬 Cold start: {type(self._decision_logic).__name__} reports '
            f'{"ACCOUNTED FOR" if verdict.accounted_for else "not accounted for"}'
            + (f' — {verdict.note}' if verdict.note else '')
        )
        return verdict

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

    def _pull_broker_balances(self) -> Optional[Dict[str, float]]:
        """
        Read the venue's balances under the shared REST ladder (§43).

        Returns:
            Asset → amount, or None when the ladder gave up
        """
        adapter = self._executor.broker.adapter
        return run_with_ladder(adapter.get_broker_balances, self._executor.get_rest_ladder())

    def _restorable_book(self, book: List[PositionCarryOver]) -> List[PositionCarryOver]:
        """
        Decide which stored notes this session may take back — before anything is applied.

        Answered here rather than inside the restore so that everything downstream (the
        cross-check, the situation handed to the algo, the run record) speaks about the SAME
        set. Deciding it in the restore left the margin case claiming a restored book it had
        just declined, complete with a fabricated shortfall in the error pot.

        Margin is not restorable: those positions sit at the venue as real objects carrying
        our tag, and they come back from there (#209). A note over them would be the older of
        two answers.

        Args:
            book: The notes read back from the carry-over

        Returns:
            The notes this session will restore; empty when it may not
        """
        if not book:
            return []

        if not self._executor.portfolio.is_spot_mode():
            self._logger.error(
                f'❌ Cold start: the carry-over holds {len(book)} position note(s) but this '
                f'session is not in spot mode — not restored, and not counted anywhere else '
                f'either. Margin positions are read from the venue (#209); a note over them '
                f'would be the older of two answers.'
            )
            return []

        return list(book)

    def _restore_position_book(self, book: List[PositionCarryOver]) -> None:
        """
        Put the bot's own note about what it holds back into the portfolio.

        This is not adoption from broker truth: a spot holding is a balance, and a balance has
        no entry price and no owner — the venue cannot describe it as a position. It is OUR
        record, derived from our own fills, so it survives a restart only because we wrote it
        down. Nothing is invented here; every value is read back.

        Called only once the boot has been allowed to proceed — see run().

        Args:
            book: The notes this session may restore (already filtered)
        """
        if not book:
            return

        try:
            self._restored_count = self._executor.portfolio.restore_position_book(book)
        except (ValueError, KeyError, TypeError) as e:
            # The payload parses as JSON and as a Pydantic model, and can still be
            # unusable: `direction`, `status` and `entry_type` are STRINGS in the note and
            # enum members in the position, so one corrupt value raises here. This store's
            # contract is that damage degrades rather than stops (an unreadable carry-over is
            # reported and treated as absent), and a boot that dies on its own note is the
            # opposite of that.
            self._logger.error(
                f'❌ Cold start: the position book could not be rebuilt ({e}) — the session '
                f'starts WITHOUT it. The venue still holds whatever the note described, so '
                f'check the account by hand: {self._store.get_state_path()}'
            )
            return
        lines = [
            f'🧬 Cold start: {self._restored_count} position(s) restored from the carry-over '
            f'— entry prices are REMEMBERED, not synthesised',
        ]
        for record in book:
            lines.append(
                f'   {record.position_id}  {record.direction} {record.lots} {record.symbol} '
                f'@ {record.entry_price}  ({record.status})'
            )
        self._logger.warning('\n'.join(lines))

    def _cross_check_book(
        self,
        book: List[PositionCarryOver],
        balances: Dict[str, float],
    ) -> float:
        """
        Hold the restored book against the venue's balance — and only report.

        The check is deliberately one-sided. The account is shared: coins beyond what our book
        claims may be the operator's or another bot's, so holding MORE than we booked is normal
        and says nothing (what a bot may use is declared capital, #489). Holding LESS is not:
        our note then claims a position the account cannot cover, which happens when someone
        sold by hand between the sessions. Adjusting the book to fit would be inventing a
        number; the divergence is reported and the note stays as written.

        Args:
            book: The restored notes
            balances: Asset → amount as the venue reports it

        Returns:
            How much the book claims beyond what the account holds, in base units; 0.0 when
            the account covers it
        """
        if not book:
            return 0.0

        base = self._executor.broker.get_symbol_specification(self._symbol).base_currency
        held = sum(
            amount for asset, amount in balances.items()
            if normalize_broker_asset(asset) == base
        )
        booked = sum(record.lots * record.contract_size for record in book)

        if booked - held > _BOOK_DUST:
            self._logger.error(
                f'❌ Cold start: the restored book claims {booked} {base} but the account '
                f'holds {held} — {booked - held} short. Something sold outside this bot '
                f'between the sessions. The book is NOT adjusted: closing against it will '
                f'fail or partially fill. Check the account by hand.'
            )
            return booked - held

        self._logger.info(
            f'🧬 Cold start: book {booked} {base} against {held} held at the venue — covered'
            + (f' ({held - booked} beyond the book, not this bot\'s to use)'
               if held - booked > _BOOK_DUST else '')
        )
        return 0.0

    def _split(
        self,
        broker_orders: List[BrokerOrder],
        known_keys: Set[str],
    ) -> Tuple[List[Tuple[str, BrokerOrder]], List[SkippedOrder]]:
        """
        Sort broker truth into what is ours and what is left alone — with the REASON.

        The reason is data, not a log line: the decision logic is handed the whole picture
        (#493) and a bot may well be able to account for an order it did NOT get. It cannot
        do that from a count.

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
            ((recovered order id, order) pairs, everything skipped with its reason)
        """
        ours: List[Tuple[str, BrokerOrder]] = []
        skipped: List[SkippedOrder] = []

        for order in broker_orders:
            parsed = parse_client_order_id(order.client_order_id)
            if parsed is None:
                skipped.append(self._skipped(order, SkipReason.FOREIGN_KEY))
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
                skipped.append(self._skipped(order, SkipReason.IN_FLIGHT))
                continue

            if order.symbol != self._symbol:
                self._logger.warning(
                    f'🧬 Cold start: a key of our shape ({order.client_order_id}) sits on a '
                    f'{order.symbol} order, but this bot trades {self._symbol} — left alone.'
                )
                skipped.append(self._skipped(order, SkipReason.OTHER_SYMBOL))
                continue

            if parsed[0] not in known_keys:
                skipped.append(self._skipped(order, SkipReason.UNKNOWN_SESSION))
                continue

            ours.append((f'pos_{self._symbol.lower()}_{parsed[1]}', order))

        return ours, skipped

    @staticmethod
    def _skipped(order: BrokerOrder, reason: SkipReason) -> SkippedOrder:
        """
        Describe one order that was left alone.

        Args:
            order: The venue's order
            reason: Why it was not adopted

        Returns:
            The read-only view handed to the algo and to the report
        """
        return SkippedOrder(
            reason=reason,
            client_order_id=order.client_order_id,
            broker_ref=order.broker_ref,
            symbol=order.symbol,
            direction=order.direction,
            order_type=order.order_type,
            lots=order.lots,
            price=order.price,
        )

    def _report_unattributable(self, skipped: List[SkippedOrder]) -> None:
        """
        Say out loud that an order may be ours and cannot be proven so.

        This is an ERROR rather than a note, and the reason is what it usually means: the
        carry-over that would identify the order is gone — lost, corrupt, or evicted — and a
        session that shrugs here goes on to trade beside its own untracked resting order while
        the log says "nothing of ours". It is not a refusal, because the order may genuinely
        belong to somebody else and refusing forever would leave the operator no way out.

        The causes, most likely first, are in `docs/architecture/data_storage_layout.md` —
        an operator who meets this once should not have to derive them.

        Args:
            skipped: Everything left alone; only the unplaceable ones are reported here
        """
        for order in [o for o in skipped if o.reason == SkipReason.UNKNOWN_SESSION]:
            self._logger.error(
                f'❌ Cold start: order {order.broker_ref} carries a client key of OUR shape '
                f'({order.client_order_id}) but a session this bot has no record of. It may be '
                f'ours from a session whose carry-over was lost or evicted, and it is NOT '
                f'adopted — check the account by hand before letting this run continue.'
            )

    def _announce(
        self,
        ours: List[Tuple[str, BrokerOrder]],
        skipped: List[SkippedOrder],
    ) -> None:
        """
        Say what was found, before anything is decided.

        Args:
            ours: The adoption candidates
            skipped: Everything left alone, with its reason
        """
        lines = [
            f'🧬 Cold start: {len(ours)} resting order(s) of ours at the broker'
            + (f', {len(skipped)} left alone' if skipped else ''),
        ]
        for order_id, order in ours:
            lines.append(
                f'   {order_id}  ({order.client_order_id})  {order.direction.value} '
                f'{order.lots} {order.symbol} @ {order.price}  ref={order.broker_ref}'
            )
        self._logger.info('\n'.join(lines))

    def _may_adopt(
        self,
        ours: List[Tuple[str, BrokerOrder]],
        verdict: Optional[ColdStartVerdict] = None,
    ) -> bool:
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

        The algo's verdict may LOOSEN this and nothing else (#493). It lifts exactly one
        refusal — the unattended one below — because that is the only place where the
        framework declines for lack of an answer rather than for lack of knowledge. It does
        not override an operator who answered "no" in person, and it cannot make the
        framework refuse: an algo that could lock itself out of starting would be a failure
        mode invisible from the outside.

        And a yes must be SPECIFIC: `_accounting_is_complete` requires the verdict to name
        every adopted order and to give a reason. Partial accounting is not accounting, and a
        bare True would be the blanket answer a mandatory hook otherwise trains people into.

        Args:
            ours: The adoption candidates as (recovered order id, broker order) pairs
            verdict: What the decision logic answered, when it was asked

        Returns:
            True when adoption may proceed
        """
        count = len(ours)
        if self._config.adoption_mode == 'auto':
            self._logger.warning(
                f'🧬 Cold start: adopting {count} order(s) automatically '
                f"(adoption_mode='auto'). No operator confirmed this."
            )
            return True

        if not self._interactive:
            if verdict is not None and verdict.accounted_for and self._accounting_is_complete(
                    ours, verdict):
                self._logger.warning(
                    f'🧬 Cold start: {count} order(s) adopted although nobody is present — '
                    f'{type(self._decision_logic).__name__} accounted for the situation'
                    + (f' ({verdict.note})' if verdict.note else '')
                    + '. The framework would have refused.'
                )
                return True

            self._logger.error(
                f'❌ COLD START ABORTED — {count} resting order(s) of ours need confirmation '
                f"and nobody declared themselves present (adoption_mode='operator_confirm'). "
                f'The session stays flat and trades nothing. Either start it with --attended '
                f"from a terminal, set adoption_mode='auto' for unattended running, or cancel "
                f'the orders at the broker.'
            )
            return False

        # The prompt says what adoption COSTS, not only what it does — and the cost now
        # depends on the session-end policy (#492), so it is read from there rather than
        # asserted. Open positions are no longer touched by the cleanup at all; only the
        # orders axis decides, and both of its values are worth knowing before confirming.
        if self._session_end_orders == 'leave':
            consequence = (
                'this session LEAVES resting orders at the venue when it ENDS '
                '(session_end.orders=leave), so adopting them means they stay live and '
                'unattended until a later session picks them up')
        else:
            consequence = (
                'this session CANCELS every resting order when it ENDS (normally or by '
                'abort, session_end.orders=cancel), so adopting them also puts them under '
                'that cleanup')
        answer = input(
            f'  ▸ Adopt {count} resting order(s) and resume?\n'
            f'    Note: {consequence}. [y/N] '
        ).strip().lower()
        if answer not in ('y', 'yes'):
            self._logger.error(
                '❌ COLD START ABORTED — the operator declined adoption. The session stays '
                'flat; the orders are untouched at the broker.'
            )
            return False
        return True

    def _accounting_is_complete(
        self,
        ours: List[Tuple[str, BrokerOrder]],
        verdict: ColdStartVerdict,
    ) -> bool:
        """
        Whether a yes is specific enough to be honoured (#493).

        Two requirements, and both exist to keep the mandatory hook from degenerating into a
        reflex `True`. The verdict has to NAME every adopted order — so the author writes a
        loop over the situation instead of a constant — and it has to give a reason, because
        a bare yes tells the operator reading the run record thirty restarts later nothing at
        all. Naming SOME of the orders is refused on purpose: partial accounting is not
        accounting, and the framework cannot adopt half a book.

        Args:
            ours: The adoption candidates
            verdict: The algo's answer

        Returns:
            True when the yes may be honoured
        """
        expected = {order_id for order_id, _ in ours}
        named = set(verdict.accounted_order_ids)

        if not verdict.note.strip():
            self._logger.error(
                f'❌ Cold start: {type(self._decision_logic).__name__} answered '
                f'accounted_for=True without a reason — not honoured. A yes that cannot be '
                f'read back is not an answer.'
            )
            return False

        missing = expected - named
        if missing:
            self._logger.error(
                f'❌ Cold start: {type(self._decision_logic).__name__} answered '
                f'accounted_for=True but did not account for {sorted(missing)} — not '
                f'honoured. Every adopted order has to be named; partial accounting is not '
                f'accounting.'
            )
            return False

        return True

    def _raise_counter_floor(
        self,
        ours: List[Tuple[str, BrokerOrder]],
        carried_floor: int,
        skipped: Optional[List[SkippedOrder]] = None,
        book: Optional[List[PositionCarryOver]] = None,
    ) -> None:
        """
        Lift the position counter above everything this bot has already minted.

        Four sources, and each covers a case the others miss:

        - ADOPTED orders recover the counters of orders still resting
        - the CARRY-OVER recovers the ones whose orders are already gone
        - the RESTORED BOOK recovers the ones a position still occupies, in case the stored
          high-water mark and the stored book ever disagree
        - SKIPPED orders of our own SHAPE recover the worst case of all: an order carrying our
          key format from a session we cannot place is one WE minted, and it turns up exactly
          when the carry-over was lost — which is the one moment `carried_floor` is 0. Reading
          those counters is what keeps the invariant in the case most likely to break it. An
          order on ANOTHER symbol is deliberately not read: `pos_<symbol>_<n>` is numbered per
          bot, so a foreign symbol's counter says nothing about ours.

        Nothing is at risk from lifting the floor too high — an id is a name, not a quantity.

        Args:
            ours: The adopted (order id, broker order) pairs
            carried_floor: The high-water mark from the carry-over
            skipped: Everything the split left alone, with its reason
            book: The positions restored from the carry-over
        """
        counters = [carried_floor]
        for order_id, _ in ours:
            counters.append(_counter_of(order_id))
        for record in book or []:
            counters.append(_counter_of(record.position_id))
        for order in skipped or []:
            if order.reason in _OUR_SHAPE_REASONS:
                parsed = parse_client_order_id(order.client_order_id)
                if parsed is not None and parsed[1].isdigit():
                    counters.append(int(parsed[1]))

        floor = max(counters)
        if floor > 0:
            self._executor.portfolio.raise_position_counter_floor(floor)
            self._logger.info(
                f'🧬 Cold start: position counter lifted to {floor} so no id is minted twice'
            )
