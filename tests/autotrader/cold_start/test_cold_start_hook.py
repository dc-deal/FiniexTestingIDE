"""
The Algo's Say at Boot — `on_cold_start` (#493)

The framework knows what it FOUND at the venue; it does not know what the strategy can cope
with. So the decision logic gets asked — and the two rules that make that safe are what this
suite pins down:

    it may only LOOSEN     the framework's refusal is the floor; True lifts it, False and
                           "no hook at all" behave identically, and no answer can make the
                           framework refuse where it would have started

    no case DISAPPEARS     the situation is reported whether the algo handled it or not, so
                           "the algo says it is fine" never becomes indistinguishable from
                           "nothing was found"

The third property is the one that is easy to get wrong: the hook is asked on EVERY boot that
found something — including `adoption_mode='auto'`, where there is no refusal to lift. That is
the mode an unattended thirty-day run uses, so a hook consulted only at the refusal would stay
silent in exactly the case it exists for.
"""

from typing import List, Optional

from python.framework.autotrader.cold_start_adopter import ColdStartAdopter
from python.framework.types.autotrader_types.cold_start_types import (
    ColdStartSituation,
    ColdStartVerdict,
    SkipReason,
)
from python.framework.types.config_types.autotrader_defaults_config_types import ColdStartDefaults
from python.framework.types.trading_env_types.order_types import OrderType
from python.framework.utils.run_id_utils import build_client_order_id
from python.framework.validators.decision_logic_hook_validator import check_cold_start_hook

from tests.autotrader.cold_start.conftest import PREVIOUS_SESSION_KEY, make_broker_order


class SpyLogic:
    """
    A decision logic stand-in that records what it was asked and answers as instructed.

    Duck-typed on purpose: the adopter needs exactly one method and the class name, and
    building a real logic would drag in workers, parameters and a trading API without
    testing any of them.
    """

    def __init__(self, verdict: Optional[ColdStartVerdict] = None, answer_garbage: bool = False):
        self._verdict = verdict or ColdStartVerdict()
        self._answer_garbage = answer_garbage
        self.situations: List[ColdStartSituation] = []

    def on_cold_start(self, situation: ColdStartSituation):
        self.situations.append(situation)
        return 'yes please' if self._answer_garbage else self._verdict


def _adopter(executor, store, logger, logic=None, mode: str = 'auto', dry_run: bool = False,
             interactive: bool = False) -> ColdStartAdopter:
    """The boot step with a decision logic attached."""
    return ColdStartAdopter(
        executor=executor,
        store=store,
        config=ColdStartDefaults(adoption_mode=mode),
        symbol='BTCUSD',
        logger=logger,
        dry_run=dry_run,
        interactive=interactive,
        decision_logic=logic,
    )


def _one_order_of_ours(executor, store, counter: int = 47) -> None:
    """A resting order from a session the carry-over remembers."""
    store.save(session_key=PREVIOUS_SESSION_KEY, highest_position_counter=0)
    key = build_client_order_id(PREVIOUS_SESSION_KEY, f'pos_btcusd_{counter}')
    executor.broker.adapter.set_broker_orders([make_broker_order('OQ7X2A-OLD', key)])


class TestTheVerdictMayOnlyLoosen:
    """One refusal can be lifted, and nothing can be tightened."""

    def test_an_accounted_for_situation_starts_where_the_framework_would_refuse(
        self, executor, store, logger
    ):
        # operator_confirm without a declared human is the unattended-boot refusal.
        _one_order_of_ours(executor, store)
        logic = SpyLogic(ColdStartVerdict(
            True, 'my swing state matches this order',
            accounted_order_ids=['pos_btcusd_47']))

        started = _adopter(executor, store, logger, logic,
                           mode='operator_confirm', interactive=False).run()

        assert started is True
        assert len(executor.get_active_orders()) == 1
        # And it is said out loud that the framework would have refused.
        assert any('would have refused' in message for message in logger.warnings)

    def test_a_declining_answer_behaves_exactly_like_no_hook(self, executor, store, logger):
        _one_order_of_ours(executor, store)
        logic = SpyLogic(ColdStartVerdict(False, 'cannot place this order'))

        started = _adopter(executor, store, logger, logic,
                           mode='operator_confirm', interactive=False).run()

        assert started is False
        assert executor.get_active_orders() == []

    def test_no_hook_at_all_still_refuses(self, executor, store, logger):
        _one_order_of_ours(executor, store)

        started = _adopter(executor, store, logger, logic=None,
                           mode='operator_confirm', interactive=False).run()

        assert started is False

    def test_a_declining_answer_cannot_stop_an_automatic_adoption(
        self, executor, store, logger
    ):
        # The floor is the framework's, and in 'auto' there is no refusal to lift. An algo
        # that could veto here would have a way to lock itself out of starting.
        _one_order_of_ours(executor, store)
        logic = SpyLogic(ColdStartVerdict(False, 'I would rather not'))

        started = _adopter(executor, store, logger, logic, mode='auto').run()

        assert started is True
        assert len(executor.get_active_orders()) == 1


class TestTheAlgoIsAlwaysTold:
    """A hook that only fires at a refusal is silent in the mode a live run actually uses."""

    def test_it_is_asked_in_auto_mode_too(self, executor, store, logger):
        _one_order_of_ours(executor, store)
        logic = SpyLogic()

        _adopter(executor, store, logger, logic, mode='auto').run()

        assert len(logic.situations) == 1

    def test_it_is_asked_even_when_nothing_of_ours_is_resting(self, executor, store, logger):
        # A stranger's order is still information: it binds capital and it sits on the same
        # account. The algo learns about it here or nowhere.
        executor.broker.adapter.set_broker_orders([make_broker_order('OQ9Z1B-EXT', None)])
        logic = SpyLogic()

        _adopter(executor, store, logger, logic).run()

        assert len(logic.situations) == 1
        assert logic.situations[0].skipped[0].reason == SkipReason.FOREIGN_KEY

    def test_an_empty_venue_asks_nothing(self, executor, store, logger):
        logic = SpyLogic()

        _adopter(executor, store, logger, logic).run()

        assert logic.situations == []

    def test_a_dry_run_asks_nothing(self, executor, store, logger):
        # The venue was never queried, so there is no situation to answer for.
        _one_order_of_ours(executor, store)
        logic = SpyLogic()

        _adopter(executor, store, logger, logic, dry_run=True).run()

        assert logic.situations == []

    def test_a_hook_answering_with_something_else_is_reported_and_read_as_no(
        self, executor, store, logger
    ):
        _one_order_of_ours(executor, store)
        logic = SpyLogic(answer_garbage=True)

        started = _adopter(executor, store, logger, logic,
                           mode='operator_confirm', interactive=False).run()

        assert started is False
        assert any('instead of a ColdStartVerdict' in message for message in logger.errors)


class TestTheSituationIsComplete:
    """A bot cannot account for what it is only given a count of."""

    def test_it_carries_the_adopted_order_with_its_recovered_id(
        self, executor, store, logger
    ):
        _one_order_of_ours(executor, store, counter=47)
        logic = SpyLogic()

        _adopter(executor, store, logger, logic).run()

        adopted = logic.situations[0].adopted
        assert [o.order_id for o in adopted] == ['pos_btcusd_47']
        assert adopted[0].order_type == OrderType.LIMIT

    def test_it_carries_every_skip_reason_separately(self, executor, store, logger):
        store.save(session_key=PREVIOUS_SESSION_KEY, highest_position_counter=0)
        other_session = build_client_order_id('9c4e', 'pos_btcusd_12')
        executor.broker.adapter.set_broker_orders([
            make_broker_order('OQ9Z1B-EXT', None),
            make_broker_order('OQ4T2K-UNK', other_session),
        ])
        logic = SpyLogic()

        _adopter(executor, store, logger, logic).run()

        reasons = {o.reason for o in logic.situations[0].skipped}
        assert reasons == {SkipReason.FOREIGN_KEY, SkipReason.UNKNOWN_SESSION}

    def test_a_clean_boot_says_so(self, executor, store, logger):
        # Somebody else's order does not make a boot unclean — it is none of this bot's
        # business, and it is still listed.
        executor.broker.adapter.set_broker_orders([make_broker_order('OQ9Z1B-EXT', None)])
        logic = SpyLogic()

        _adopter(executor, store, logger, logic).run()

        assert logic.situations[0].is_clean() is True

    def test_an_unplaceable_key_of_our_shape_makes_it_unclean(self, executor, store, logger):
        store.save(session_key=PREVIOUS_SESSION_KEY, highest_position_counter=0)
        executor.broker.adapter.set_broker_orders([
            make_broker_order('OQ4T2K-UNK', build_client_order_id('9c4e', 'pos_btcusd_12')),
        ])
        logic = SpyLogic()

        _adopter(executor, store, logger, logic).run()

        assert logic.situations[0].is_clean() is False

    def test_it_carries_the_policy_and_whether_anybody_is_watching(
        self, executor, store, logger, monkeypatch
    ):
        # A declared human means the gate asks, so the answer has to be scripted — the
        # situation is built before the prompt either way.
        monkeypatch.setattr('builtins.input', lambda *_: 'y')
        _one_order_of_ours(executor, store)
        logic = SpyLogic()

        _adopter(executor, store, logger, logic,
                 mode='operator_confirm', interactive=True).run()

        situation = logic.situations[0]
        assert situation.adoption_mode == 'operator_confirm'
        assert situation.attended is True
        assert situation.symbol == 'BTCUSD'


class TestTheContractIsEnforcedAtStartup:
    """The declaration decides, not the pipeline: what can rest can be found resting."""

    def test_a_logic_declaring_a_resting_type_must_answer(self):
        class RestingBot:
            pass

        message = check_cold_start_hook(RestingBot, [OrderType.MARKET, OrderType.LIMIT])

        assert message is not None
        assert 'on_cold_start' in message

    def test_a_market_only_logic_is_not_asked(self):
        class MarketBot:
            pass

        assert check_cold_start_hook(MarketBot, [OrderType.MARKET]) is None

    def test_an_answer_of_the_right_shape_passes(self):
        class RestingBot:
            def on_cold_start(self, situation):
                return ColdStartVerdict()

        assert check_cold_start_hook(RestingBot, [OrderType.STOP]) is None

    def test_a_hook_the_framework_cannot_call_is_caught_before_the_venue_is_asked(self):
        class RestingBot:
            def on_cold_start(self):
                return ColdStartVerdict()

        message = check_cold_start_hook(RestingBot, [OrderType.LIMIT])

        assert message is not None
        assert 'cannot call' in message

    def test_legal_signatures_are_not_rejected(self):
        # A trial bind rather than a parameter count, because counting rejects shapes that
        # work perfectly well — and a startup that refuses a working hook is worse than one
        # that never checked.
        class WithDefault:
            def on_cold_start(self, situation, verbose=False):
                return ColdStartVerdict()

        class UnconventionalSelf:
            def on_cold_start(zelf, situation):
                return ColdStartVerdict()

        class VarArgs:
            def on_cold_start(self, *args):
                return ColdStartVerdict()

        for logic in (WithDefault, UnconventionalSelf, VarArgs):
            assert check_cold_start_hook(logic, [OrderType.LIMIT]) is None, logic.__name__


class TestAYesHasToBeSpecific:
    """
    A mandatory hook trains people into a reflex `True` unless the yes costs something.

    So it costs two things: NAME every adopted order, and give a reason. Both are refusals to
    honour, not refusals to start — the framework simply falls back to its own policy.
    """

    def test_a_yes_that_names_no_order_is_not_honoured(self, executor, store, logger):
        _one_order_of_ours(executor, store)
        logic = SpyLogic(ColdStartVerdict(True, 'trust me'))

        started = _adopter(executor, store, logger, logic,
                           mode='operator_confirm', interactive=False).run()

        assert started is False
        assert any('did not account for' in message for message in logger.errors)

    def test_a_yes_that_names_only_some_orders_is_not_honoured(self, executor, store, logger):
        # Partial accounting is not accounting: the framework cannot adopt half a book.
        store.save(session_key=PREVIOUS_SESSION_KEY, highest_position_counter=0)
        executor.broker.adapter.set_broker_orders([
            make_broker_order('OQ7X2A-A',
                              build_client_order_id(PREVIOUS_SESSION_KEY, 'pos_btcusd_47')),
            make_broker_order('OQ7X2A-B',
                              build_client_order_id(PREVIOUS_SESSION_KEY, 'pos_btcusd_48')),
        ])
        logic = SpyLogic(ColdStartVerdict(True, 'I know the first one',
                                          accounted_order_ids=['pos_btcusd_47']))

        started = _adopter(executor, store, logger, logic,
                           mode='operator_confirm', interactive=False).run()

        assert started is False
        assert any('pos_btcusd_48' in message for message in logger.errors)

    def test_a_yes_without_a_reason_is_not_honoured(self, executor, store, logger):
        # A yes that cannot be read back in the run record thirty restarts later is not an
        # answer, however complete its id list is.
        _one_order_of_ours(executor, store)
        logic = SpyLogic(ColdStartVerdict(True, '   ',
                                          accounted_order_ids=['pos_btcusd_47']))

        started = _adopter(executor, store, logger, logic,
                           mode='operator_confirm', interactive=False).run()

        assert started is False
        assert any('without a reason' in message for message in logger.errors)

    def test_a_complete_yes_is_honoured(self, executor, store, logger):
        store.save(session_key=PREVIOUS_SESSION_KEY, highest_position_counter=0)
        executor.broker.adapter.set_broker_orders([
            make_broker_order('OQ7X2A-A',
                              build_client_order_id(PREVIOUS_SESSION_KEY, 'pos_btcusd_47')),
            make_broker_order('OQ7X2A-B',
                              build_client_order_id(PREVIOUS_SESSION_KEY, 'pos_btcusd_48')),
        ])
        logic = SpyLogic(ColdStartVerdict(
            True, 'both match my persisted levels',
            accounted_order_ids=['pos_btcusd_47', 'pos_btcusd_48']))

        started = _adopter(executor, store, logger, logic,
                           mode='operator_confirm', interactive=False).run()

        assert started is True
        assert len(executor.get_active_orders()) == 2
