"""
Cold Start — the Boot Wiring (#355 Phase 2)

Two properties of `AutotraderMain` that no unit of the adopter or the store can hold on its
own, and both are refusals to write:

  a REFUSED boot appends no session key. Otherwise a restart loop feeds on itself: the refusal
  grades the run non-zero, a supervisor relaunches, each boot appends its own fresh key, and
  ten aborts later the key that OWNS the order it kept refusing over has been evicted — after
  which the bot stops refusing and trades beside it.

  a DRY RUN appends no session key either. It sent no order to any venue, so its key is not one
  this bot "sent orders under".

Exercised through `__new__`: the method needs three attributes, and building a real session
would drag in a broker, a tick source and a decision logic without testing any of them.
"""

from types import SimpleNamespace
from typing import List, Optional, Set

from python.framework.autotrader.autotrader_main import AutotraderMain
from python.framework.autotrader.cold_start_setup import ColdStartSetup, setup_cold_start


class SpyStore:
    """Records what would be persisted."""

    def __init__(self):
        self.saves: List[dict] = []

    def save(self, session_key: str, highest_position_counter: int,
             keys_in_use: Optional[Set[str]] = None) -> None:
        self.saves.append({
            'session_key': session_key,
            'highest_position_counter': highest_position_counter,
            'keys_in_use': set(keys_in_use or ()),
        })


def _profile(cold_start_config, enabled: bool = True, tmp: str = '/tmp/cold_start_test'):
    """A minimal profile stand-in carrying only what the gating reads."""
    cold_start_config = cold_start_config.model_copy(update={'enabled': enabled, 'path': tmp})
    return SimpleNamespace(
        cold_start=cold_start_config,
        adapter_type='mock',
        name='btcusd_test',
        symbol='BTCUSD',
    )


def _main(executor, store, persist: bool, keys_in_use=None) -> AutotraderMain:
    """A session object carrying only what the carry-over write reads."""
    main = AutotraderMain.__new__(AutotraderMain)
    main._executor = executor
    main._cold_start = ColdStartSetup(
        proceed=True,
        store=store,
        persist=persist,
        keys_in_use=set(keys_in_use or ()),
    )
    return main


class TestNothingIsWrittenWithoutPermission:
    """The flag is set only after adoption has actually gone through."""

    def test_a_refused_or_dry_boot_writes_nothing(self, executor):
        store = SpyStore()

        _main(executor, store, persist=False)._persist_cold_start_carry_over()

        assert store.saves == []

    def test_no_store_is_a_no_op(self, executor):
        # A Field Study session, or cold_start disabled: no store was ever built.
        _main(executor, None, persist=True)._persist_cold_start_carry_over()


class TestWhatIsWritten:
    """A boot that got through records its key, its counter and what the venue is holding."""

    def test_the_key_counter_and_protected_set_all_reach_the_store(self, executor):
        store = SpyStore()
        executor.portfolio.raise_position_counter_floor(47)

        _main(executor, store, persist=True,
              keys_in_use={'8b3f'})._persist_cold_start_carry_over()

        assert len(store.saves) == 1
        saved = store.saves[0]
        assert saved['highest_position_counter'] == 47
        assert saved['keys_in_use'] == {'8b3f'}
        # The mock executor stamps no wire key, and that is recorded honestly as empty.
        assert saved['session_key'] == executor.get_session_key()


class TestWhoIsEligible:
    """
    The gating that moved out of `run()` into `setup_cold_start`.

    Three sessions have no cold start at all, and each for its own reason — so each is
    asserted, rather than trusting one branch to stand for the others.
    """

    def test_disabled_by_config_does_nothing(self, executor, config, logger):
        setup = setup_cold_start(
            config=_profile(config, enabled=False), executor=executor,
            decision_logic=None, logger=logger, run_id='r1', attended=False,
            field_study_active=False, dry_run=True)

        assert setup.proceed is True
        assert setup.store is None
        assert setup.persist is False

    def test_the_field_study_is_skipped_and_said_out_loud(self, executor, config, logger):
        # It funds both sides on purpose and asserts its own flat order book (#332).
        setup = setup_cold_start(
            config=_profile(config), executor=executor, decision_logic=None, logger=logger,
            run_id='r1', attended=False, field_study_active=True, dry_run=False)

        assert setup.store is None
        assert any('Field Study' in i for i in logger.infos)

    def test_a_simulation_executor_has_no_broker_truth(self, config, logger):
        setup = setup_cold_start(
            config=_profile(config), executor=object(), decision_logic=None, logger=logger,
            run_id='r1', attended=False, field_study_active=False, dry_run=False)

        assert setup.proceed is True
        assert setup.store is None
