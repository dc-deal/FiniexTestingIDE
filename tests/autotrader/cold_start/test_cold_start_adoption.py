"""
Cold-Start Adoption — Rebuilding What Is Provably Ours (#355 Phase 2)

A restarted bot starts with an empty shadow while the venue still holds what its predecessor
left resting. This suite covers the boot step that rebuilds it, and the line it must not cross:

    our key, from a session the carry-over knows   → ADOPT
    a key of our shape from another client         → leave alone
    no key at all                                  → leave alone
    a balance                                      → never adopted here at all — a coin
                                                     carries no owner tag, so ownership would
                                                     be a guess (that is capital allocation)

The refusal cases matter as much as the adoption: `operator_confirm` without a terminal must
refuse and stay flat rather than wait for an answer nobody is there to give, and an unreachable
venue must stop the boot rather than let it start blind.
"""

from python.framework.autotrader.cold_start_adopter import ColdStartAdopter
from python.framework.exceptions.connection_errors import ConnectionAttemptFailedError
from python.framework.types.autotrader_types.cold_start_types import SkipReason
from python.framework.types.config_types.autotrader_defaults_config_types import ColdStartDefaults
from python.framework.types.trading_env_types.order_types import OrderType
from python.framework.utils.run_id_utils import build_client_order_id
from tests.autotrader.cold_start.conftest import PREVIOUS_SESSION_KEY, make_broker_order


def _adopter(executor, store, logger, config=None, dry_run=False, interactive=False):
    """The boot step under test, wired to the real executor."""
    return ColdStartAdopter(
        executor=executor,
        store=store,
        config=config or ColdStartDefaults(adoption_mode='auto'),
        symbol='BTCUSD',
        logger=logger,
        dry_run=dry_run,
        interactive=interactive,
    )


def _remember_previous_session(store, counter: int = 0) -> None:
    """Write the carry-over an earlier session would have left behind."""
    store.save(session_key=PREVIOUS_SESSION_KEY, highest_position_counter=counter)


class TestOwnershipDecides:
    """What gets adopted, and — more importantly — what does not."""

    def test_an_order_from_a_known_earlier_session_is_adopted(
        self, executor, store, logger, config
    ):
        _remember_previous_session(store)
        key = build_client_order_id(PREVIOUS_SESSION_KEY, 'pos_btcusd_47')
        executor.broker.adapter.set_broker_orders([make_broker_order('OQ7X2A-OLD', key)])

        assert _adopter(executor, store, logger).run() is True

        active = executor.get_active_orders()
        assert len(active) == 1
        # The internal id is RECOVERED from the key's counter, not invented.
        assert active[0].pending_order_id == 'pos_btcusd_47'
        assert active[0].broker_ref == 'OQ7X2A-OLD'

    def test_a_foreign_clients_key_is_left_alone(self, executor, store, logger):
        # Same SHAPE, a session this bot never sent under. Claiming it would be claiming an
        # order with an owner on the other side.
        _remember_previous_session(store)
        executor.broker.adapter.set_broker_orders([
            make_broker_order('OQ7X2A-OTHER', build_client_order_id('ffff', 'pos_btcusd_9')),
        ])

        assert _adopter(executor, store, logger).run() is True
        assert executor.get_active_orders() == []

    def test_an_order_with_no_key_is_left_alone(self, executor, store, logger):
        _remember_previous_session(store)
        executor.broker.adapter.set_broker_orders([make_broker_order('OQ7X2A-MANUAL', None)])

        assert _adopter(executor, store, logger).run() is True
        assert executor.get_active_orders() == []

    def test_without_a_carry_over_nothing_is_recognised(self, executor, store, logger):
        # First ever boot: the bot has no record of any session, so even an order carrying our
        # exact format cannot be attributed to us. Honest, and the reason the carry-over exists.
        executor.broker.adapter.set_broker_orders([
            make_broker_order('OQ7X2A-OLD',
                              build_client_order_id(PREVIOUS_SESSION_KEY, 'pos_btcusd_47')),
        ])

        assert _adopter(executor, store, logger).run() is True
        assert executor.get_active_orders() == []

    def test_a_clean_venue_boots_silently(self, executor, store, logger):
        executor.broker.adapter.set_broker_orders([])

        assert _adopter(executor, store, logger).run() is True
        assert executor.get_active_orders() == []
        assert logger.errors == []


class TestOnlyRestingOrdersAreAdopted:
    """
    A MARKET order in the open list is in FLIGHT, not resting — and it is the one case where
    the wire key is ambiguous.

    The key names a POSITION: a close order carries the key of the position it closes, so it
    is indistinguishable from that position's entry order. Adopting it would also put a market
    order into the resting-order world, where nothing ever triggers it. Both reasons point the
    same way, so the order type decides and the key ambiguity never gets to matter.
    """

    def test_a_market_order_is_reported_not_adopted(self, executor, store, logger):
        _remember_previous_session(store)
        order = make_broker_order(
            'OQ7X2A-INFLIGHT', build_client_order_id(PREVIOUS_SESSION_KEY, 'pos_btcusd_47'))
        order.order_type = OrderType.MARKET
        executor.broker.adapter.set_broker_orders([order])

        assert _adopter(executor, store, logger).run() is True
        assert executor.get_active_orders() == []
        assert any('in flight at the venue' in w for w in logger.warnings)

    def test_a_limit_order_is_adopted(self, executor, store, logger):
        # The counter-case, so the guard cannot pass by rejecting everything.
        _remember_previous_session(store)
        executor.broker.adapter.set_broker_orders([
            make_broker_order('OQ7X2A-RESTING',
                              build_client_order_id(PREVIOUS_SESSION_KEY, 'pos_btcusd_47')),
        ])

        assert _adopter(executor, store, logger).run() is True
        assert len(executor.get_active_orders()) == 1


    def test_a_stop_order_is_adopted_into_the_stop_world(self, executor, store, logger):
        """
        A stop is not a limit, and filing it as one makes it unmanageable (#500).

        Every adopted order used to land in `_active_limit_orders` regardless of type,
        while the type filter here admits STOP and STOP_LIMIT on purpose. The consequences
        were all silent: `cancel_stop_order` and `modify_stop_order` search the stop world
        and would not find it, `modify_limit_order` WOULD and would amend its TRIGGER as a
        limit price, and the session-end cleanup read only the limit list.
        """
        _remember_previous_session(store)
        order = make_broker_order(
            'OQ7X2A-STOP',
            build_client_order_id(PREVIOUS_SESSION_KEY, 'pos_btcusd_47'))
        order.order_type = OrderType.STOP
        order.price = None
        order.stop_price = 65000.0
        executor.broker.adapter.set_broker_orders([order])

        assert _adopter(executor, store, logger).run() is True

        counts = executor.get_active_order_counts()
        assert counts['active_stops'] == 1, f'adopted into the wrong world: {counts}'
        assert counts['active_limits'] == 0
        adopted = executor.get_active_orders()[0]
        assert adopted.entry_price == 65000.0, (
            'a resting stop is held at its TRIGGER, the same convention the simulation uses')

    def test_a_stop_limit_carries_both_of_its_prices(self, executor, store, logger):
        """The trigger rests, the limit rides along — and neither may take the other's slot."""
        _remember_previous_session(store)
        order = make_broker_order(
            'OQ7X2A-STOPLIMIT',
            build_client_order_id(PREVIOUS_SESSION_KEY, 'pos_btcusd_47'))
        order.order_type = OrderType.STOP_LIMIT
        order.stop_price = 65000.0
        order.price = 65100.0
        executor.broker.adapter.set_broker_orders([order])

        assert _adopter(executor, store, logger).run() is True

        adopted = executor.get_active_orders()[0]
        assert adopted.entry_price == 65000.0, 'the trigger is what it rests on'
        assert adopted.order_kwargs['limit_price'] == 65100.0


class TestAdoptionStatesItsConsequence:
    """An adopted order blocks the usual algo gate, and the operator must be able to read that."""

    def test_the_pending_gate_consequence_is_named(self, executor, store, logger):
        _remember_previous_session(store)
        executor.broker.adapter.set_broker_orders([
            make_broker_order('OQ7X2A-RESTING',
                              build_client_order_id(PREVIOUS_SESSION_KEY, 'pos_btcusd_47')),
        ])

        _adopter(executor, store, logger).run()

        assert any('has_pending_orders() is true' in w for w in logger.warnings)
        # And it is really true, which is why saying it matters.
        assert executor.has_pending_orders() is True


class TestPartiallyFilled:
    """An order the venue has already partly executed is adopted as partly executed."""

    def test_the_executed_part_is_carried_over(self, executor, store, logger):
        # Kraken reports `vol` (original) and `vol_exec` (already traded) separately. Adopting
        # only `lots` would rebuild the order at full size and overstate the shadow by exactly
        # the part that already traded.
        _remember_previous_session(store)
        order = make_broker_order(
            'OQ7X2A-HALF', build_client_order_id(PREVIOUS_SESSION_KEY, 'pos_btcusd_47'),
            lots=0.02)
        order.filled_lots = 0.008
        executor.broker.adapter.set_broker_orders([order])

        _adopter(executor, store, logger).run()

        adopted = executor.get_active_orders()[0]
        assert adopted.lots == 0.02
        assert adopted.fills.cumulative_filled_lots == 0.008


class TestUnattributable:
    """
    Our key SHAPE, a session we cannot place — the third answer, not the second.

    This is what a lost or EVICTED carry-over looks like from the outside, and the review
    found the silent version: reported as merely "foreign", the boot says "nothing of ours"
    and trades beside an order that may well be its own.
    """

    def test_it_is_neither_adopted_nor_called_foreign(self, executor, store, logger):
        _remember_previous_session(store)
        # A key of our shape whose session the carry-over does not know.
        executor.broker.adapter.set_broker_orders([
            make_broker_order('OQ7X2A-LOST', build_client_order_id('c0de', 'pos_btcusd_9')),
        ])

        proceeded = _adopter(executor, store, logger).run()

        assert proceeded is True
        assert executor.get_active_orders() == []
        # It reaches the error pot — a session in this state must not grade green (§35).
        assert any('OUR shape' in e for e in logger.errors)
        assert any('OQ7X2A-LOST' in e for e in logger.errors)

    def test_its_key_is_protected_from_eviction(self, executor, store, logger):
        # Even unattributable, the key is at the venue — the carry-over must not drop it.
        _remember_previous_session(store)
        executor.broker.adapter.set_broker_orders([
            make_broker_order('OQ7X2A-LOST', build_client_order_id('c0de', 'pos_btcusd_9')),
        ])
        adopter = _adopter(executor, store, logger)
        adopter.run()

        assert 'c0de' in adopter.get_venue_session_keys()

    def test_a_key_of_ours_on_another_symbol_is_left_alone(self, executor, store, logger):
        # The venue's open-order list is account-wide. Adopting a foreign symbol would build
        # an id from OUR symbol and then crash the fill path, which requires the tick and the
        # order to agree on the instrument.
        _remember_previous_session(store)
        executor.broker.adapter.set_broker_orders([
            make_broker_order('OQ7X2A-ETH',
                              build_client_order_id(PREVIOUS_SESSION_KEY, 'pos_ethusd_3'),
                              symbol='ETHUSD'),
        ])

        assert _adopter(executor, store, logger).run() is True
        assert executor.get_active_orders() == []
        assert any('ETHUSD' in w for w in logger.warnings)


class TestWhoseKeyItWas:
    """
    The skip bucket records OWNERSHIP, not just a reason (#489).

    `_split` tests the symbol BEFORE the session key, so an `other_symbol` order may carry
    either our own key or a stranger's — and the reason string answers neither. Anything that
    reasons about ownership needs the fact: a sibling bot of this framework on another
    instrument is a shared account, our own re-pointed profile is not.
    """

    def _skipped(self, executor, store, logger):
        """
        The single skipped order of a boot. Args: the fixtures. Returns: the SkippedOrder.
        """
        adopter = _adopter(executor, store, logger)
        adopter.run()
        skipped = adopter.get_situation().skipped
        assert len(skipped) == 1
        return skipped[0]

    def test_our_own_key_on_another_symbol_is_recorded_as_ours(self, executor, store, logger):
        _remember_previous_session(store)
        executor.broker.adapter.set_broker_orders([
            make_broker_order('OQ7X2A-ETH',
                              build_client_order_id(PREVIOUS_SESSION_KEY, 'pos_ethusd_3'),
                              symbol='ETHUSD'),
        ])

        order = self._skipped(executor, store, logger)

        assert order.reason is SkipReason.OTHER_SYMBOL
        assert order.key_is_ours is True
        # The log says whose it is, because that is what an operator needs to act on.
        assert any('OUR OWN' in w for w in logger.warnings)

    def test_a_stranger_key_on_another_symbol_is_recorded_as_foreign(
            self, executor, store, logger):
        """The shared-account case: another instance of this framework, another instrument."""
        _remember_previous_session(store)
        executor.broker.adapter.set_broker_orders([
            make_broker_order('OQ7X2A-ETH',
                              build_client_order_id('f00d', 'pos_ethusd_3'),
                              symbol='ETHUSD'),
        ])

        order = self._skipped(executor, store, logger)

        assert order.reason is SkipReason.OTHER_SYMBOL
        assert order.key_is_ours is False
        assert any('another session' in w for w in logger.warnings)

    def test_an_order_with_no_key_of_our_shape_judges_nothing(self, executor, store, logger):
        """There is no key to compare, so the answer is None rather than a guessed False."""
        _remember_previous_session(store)
        executor.broker.adapter.set_broker_orders([
            make_broker_order('OQ7X2A-HUMAN', 'somebody-elses-format'),
        ])

        order = self._skipped(executor, store, logger)

        assert order.reason is SkipReason.FOREIGN_KEY
        assert order.key_is_ours is None

    def test_an_unknown_session_is_recorded_as_not_ours(self, executor, store, logger):
        """Our shape, a session we cannot place — still an ERROR, still not a refusal."""
        _remember_previous_session(store)
        executor.broker.adapter.set_broker_orders([
            make_broker_order('OQ7X2A-LOST', build_client_order_id('c0de', 'pos_btcusd_9')),
        ])

        order = self._skipped(executor, store, logger)

        assert order.reason is SkipReason.UNKNOWN_SESSION
        assert order.key_is_ours is False


class TestADeclaredExclusiveAccount:
    """
    What `capital.exclusive_account: true` changes, and what it deliberately does not (#489).

    The declaration is the operator's, because a framework cannot read exclusivity off a
    venue — a balance carries no owner tag. What the framework does with it is graded by
    CONSEQUENCE: a stranger's order on the instrument this bot trades is the case where the
    two exposures merge into one balance no tag can split; a stranger elsewhere means the
    declaration is wrong, which is worth an alert and not worth dying over.
    """

    def _run(self, executor, store, logger, exclusive: bool) -> bool:
        """
        Boot with or without the declaration.

        Args:
            executor, store, logger: the fixtures
            exclusive: what the profile declares

        Returns:
            Whether the boot allowed the session to start
        """
        _remember_previous_session(store)
        return _adopter(executor, store, logger).run() if not exclusive else ColdStartAdopter(
            executor=executor, store=store, config=ColdStartDefaults(adoption_mode='auto'),
            symbol='BTCUSD', logger=logger, exclusive_account=True,
        ).run()

    def test_a_stranger_on_our_own_symbol_reaches_the_error_pot_and_starts(
            self, executor, store, logger):
        """
        The loudest case, and it still STARTS — refusing is not the safe outcome.

        A refusal would abandon this bot's own position and its protective resting order at
        the venue with nothing reconciling them, and on spot the SL/TP level lives in the
        very process that declined to start. It would also deadlock two sibling bots and
        loop against a supervisor. #499 turns this into a HALT that keeps managing what is
        ours while placing nothing new; until that state exists, the honest answer is an
        ERROR the operator cannot miss.
        """
        executor.broker.adapter.set_broker_orders([
            make_broker_order('OQ7X2A-HUMAN', 'somebody-elses-format', symbol='BTCUSD'),
        ])

        assert self._run(executor, store, logger, exclusive=True) is True
        assert any('exclusive_account=true' in e for e in logger.errors)
        # Both ways out are named, because the operator has to pick one.
        assert any('cancel those orders' in e for e in logger.errors)
        # And the issue that will change this is named, so nobody re-derives the reasoning.
        assert any('#499' in e for e in logger.errors)

    def test_the_same_stranger_says_nothing_without_the_declaration(
            self, executor, store, logger):
        """The default: nothing changes for any profile that never declared exclusivity."""
        executor.broker.adapter.set_broker_orders([
            make_broker_order('OQ7X2A-HUMAN', 'somebody-elses-format', symbol='BTCUSD'),
        ])

        assert self._run(executor, store, logger, exclusive=False) is True
        assert not any('exclusive_account' in e for e in logger.errors)

    def test_a_stranger_on_another_symbol_is_a_warning_not_an_error(
            self, executor, store, logger):
        """
        Graded: elsewhere means the DECLARATION is wrong, which is worth an alert.

        It is not the same weight as a stranger on our own instrument, where the two
        exposures merge into one balance no tag can split.
        """
        executor.broker.adapter.set_broker_orders([
            make_broker_order('OQ7X2A-ETH', 'somebody-elses-format', symbol='ETHUSD'),
        ])

        assert self._run(executor, store, logger, exclusive=True) is True
        assert any('declaration' in w for w in logger.warnings)
        assert not any('exclusive_account=true' in e for e in logger.errors)

    def test_an_unknown_session_of_our_shape_still_does_not_refuse(
            self, executor, store, logger):
        """
        Its key HAS our shape, so on an exclusive account it is more likely OURS, not less.

        Refusing would be the self-lockout `_report_unattributable` already argues against:
        the carry-over may simply have been lost, and refusing forever leaves no way out.
        """
        executor.broker.adapter.set_broker_orders([
            make_broker_order('OQ7X2A-LOST', build_client_order_id('c0de', 'pos_btcusd_9')),
        ])

        assert self._run(executor, store, logger, exclusive=True) is True

    def test_a_clean_exclusive_account_starts_untouched(self, executor, store, logger):
        executor.broker.adapter.set_broker_orders([])

        assert self._run(executor, store, logger, exclusive=True) is True


class TestPositionCounter:
    """The id collision that adoption would otherwise create."""

    def test_the_counter_clears_an_adopted_id(self, executor, store, logger):
        _remember_previous_session(store)
        key = build_client_order_id(PREVIOUS_SESSION_KEY, 'pos_btcusd_1')
        executor.broker.adapter.set_broker_orders([make_broker_order('OQ7X2A-OLD', key)])

        _adopter(executor, store, logger).run()

        # Without the floor this would be 'pos_btcusd_1' — the very id just adopted.
        assert executor.portfolio.get_next_position_id('BTCUSD') == 'pos_btcusd_2'

    def test_the_carry_over_alone_lifts_the_counter(self, executor, store, logger):
        # The predecessor's orders are all gone, so adoption recovers nothing — but its ids
        # were still used, and a diagnostics reader joining run records needs them unique.
        _remember_previous_session(store, counter=12)
        executor.broker.adapter.set_broker_orders([])

        _adopter(executor, store, logger).run()

        assert executor.portfolio.get_next_position_id('BTCUSD') == 'pos_btcusd_13'


class TestRefusals:
    """A boot that cannot be answered for must not become a boot that trades."""

    def test_operator_confirm_without_a_terminal_refuses(self, executor, store, logger):
        _remember_previous_session(store)
        key = build_client_order_id(PREVIOUS_SESSION_KEY, 'pos_btcusd_47')
        executor.broker.adapter.set_broker_orders([make_broker_order('OQ7X2A-OLD', key)])

        proceeded = _adopter(
            executor, store, logger,
            config=ColdStartDefaults(adoption_mode='operator_confirm'),
            interactive=False,
        ).run()

        assert proceeded is False
        assert executor.get_active_orders() == []
        # The refusal reaches the error pot, so the session cannot grade green (§35).
        assert any('COLD START ABORTED' in e for e in logger.errors)
        assert any("adoption_mode='auto'" in e for e in logger.errors)

    def test_auto_adopts_and_says_nobody_confirmed(self, executor, store, logger):
        _remember_previous_session(store)
        key = build_client_order_id(PREVIOUS_SESSION_KEY, 'pos_btcusd_47')
        executor.broker.adapter.set_broker_orders([make_broker_order('OQ7X2A-OLD', key)])

        proceeded = _adopter(
            executor, store, logger, config=ColdStartDefaults(adoption_mode='auto')).run()

        assert proceeded is True
        assert len(executor.get_active_orders()) == 1
        assert any('No operator confirmed this' in w for w in logger.warnings)

    def test_an_unreachable_venue_stops_the_boot(self, executor, store, logger, monkeypatch):
        # Starting blind is exactly what this step exists to prevent, so an unreadable venue
        # is a refusal rather than an empty result treated as "nothing there".
        def unreachable():
            raise ConnectionAttemptFailedError('broker unreachable', broker_ref='',
                                               operation='get_broker_orders')

        monkeypatch.setattr(executor.broker.adapter, 'get_broker_orders', unreachable)

        assert _adopter(executor, store, logger).run() is False
        assert any('COLD START ABORTED' in e for e in logger.errors)


class TestTheKeyIsRecordedEarly:
    """
    The carry-over is written at BOOT, not only at shutdown.

    A hard kill — SIGKILL, OOM, power — is precisely the case this carry-over exists for, and a
    shutdown-only write loses the key exactly then: the successor would find its own
    predecessor's resting orders and, unable to recognise the key, report them as a stranger's.
    Writing early can at worst record a key that placed nothing, which costs a fruitless
    lookup. The asymmetry is what decides it.

    Wired in `AutotraderMain._persist_cold_start_carry_over`, called right after adoption and
    again at shutdown; this test pins the STORE side of that contract — the key of a session
    that has not ended yet is already readable.
    """

    def test_a_key_written_at_boot_is_readable_before_shutdown(self, store):
        store.save(session_key='1641', highest_position_counter=0)

        # A successor booting after a hard kill reads exactly this.
        assert store.load().session_keys == ['1641']

    def test_a_later_write_adds_the_counter_without_losing_the_key(self, store):
        store.save(session_key='1641', highest_position_counter=0)   # boot
        store.save(session_key='1641', highest_position_counter=47)  # shutdown

        payload = store.load()
        assert payload.session_keys == ['1641']
        assert payload.highest_position_counter == 47


class TestDryRun:
    """
    A rehearsal says what it CANNOT see, and claims nothing else.

    The trap the review found: a dry run cannot query the venue at all — the adapter
    short-circuits its private reads behind a sentinel and answers with an empty list. Saying
    "nothing of ours is resting" on that basis is a statement about a venue nobody asked, made
    in exactly the mode an operator uses to rehearse. Separating "do not WRITE" from "do not
    LOOK" is #304's job; until then this mode declares its own blindness.
    """

    def test_it_adopts_nothing_and_claims_nothing(self, executor, store, logger):
        _remember_previous_session(store)
        key = build_client_order_id(PREVIOUS_SESSION_KEY, 'pos_btcusd_47')
        executor.broker.adapter.set_broker_orders([make_broker_order('OQ7X2A-OLD', key)])

        proceeded = _adopter(executor, store, logger, dry_run=True).run()

        assert proceeded is True
        assert executor.get_active_orders() == []
        assert any('NOT queried' in i for i in logger.infos)
        # And it must NOT assert the opposite.
        assert not any('nothing of ours resting' in i for i in logger.infos)

    def test_the_counter_floor_still_applies(self, executor, store, logger):
        # A rehearsal must not mint ids its predecessor already used either.
        _remember_previous_session(store, counter=12)
        executor.broker.adapter.set_broker_orders([])

        _adopter(executor, store, logger, dry_run=True).run()

        assert executor.portfolio.get_next_position_id('BTCUSD') == 'pos_btcusd_13'
