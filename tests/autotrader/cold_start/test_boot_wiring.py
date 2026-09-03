"""
Cold Start — the Boot Wiring (#355 Phase 2)

Properties of `AutotraderMain` that no unit of the adopter or the store can hold on its own.
The first two are refusals to write:

  a REFUSED boot appends no session key. Otherwise a restart loop feeds on itself: the refusal
  grades the run non-zero, a supervisor relaunches, each boot appends its own fresh key, and
  ten aborts later the key that OWNS the order it kept refusing over has been evicted — after
  which the bot stops refusing and trades beside it.

  a DRY RUN appends no session key either. It sent no order to any venue, so its key is not one
  this bot "sent orders under".

And the write itself carries the open position book — but only in SPOT mode, where a holding
is a balance the venue cannot describe as a position. In margin mode the positions sit at the
venue as real objects (#209), so the book is left alone rather than overwritten with a note.

Exercised through `__new__`: the method needs three attributes, and building a real session
would drag in a broker, a tick source and a decision logic without testing any of them.
"""

from types import SimpleNamespace
from typing import List, Optional, Set

from python.framework.autotrader.autotrader_main import AutotraderMain
from python.framework.autotrader.autotrader_tick_loop import AutotraderTickLoop
from python.framework.autotrader.cold_start_setup import ColdStartSetup, setup_cold_start
from python.framework.persistence.position_book_watcher import PositionBookWatcher
from python.framework.types.persistence_types import PositionCarryOver
from tests.autotrader.cold_start.conftest import RecordingLogger


class SpyStore:
    """Records what would be persisted."""

    def __init__(self, fail: bool = False):
        self.saves: List[dict] = []
        self._fail = fail

    def save(self, session_key: str, highest_position_counter: int,
             keys_in_use: Optional[Set[str]] = None,
             open_positions: Optional[List[PositionCarryOver]] = None,
             refresh_index: bool = True) -> None:
        if self._fail:
            raise OSError('disk full')
        self.saves.append({
            'session_key': session_key,
            'highest_position_counter': highest_position_counter,
            'keys_in_use': set(keys_in_use or ()),
            'open_positions': open_positions,
            'refresh_index': refresh_index,
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


def _note() -> PositionCarryOver:
    """One remembered spot position, as an earlier session would have written it down."""
    return PositionCarryOver(
        position_id='pos_btcusd_47',
        symbol='BTCUSD',
        direction='long',
        lots=0.01,
        original_lots=0.01,
        entry_price=61200.0,
        entry_time='2026-09-01T12:00:00+00:00',
        entry_type='market',
        contract_size=1,
    )


def _main(executor, store, persist: bool, keys_in_use=None) -> AutotraderMain:
    """A session object carrying only what the carry-over write reads."""
    main = AutotraderMain.__new__(AutotraderMain)
    main._executor = executor
    # The write promises to swallow its own failures into the session channel (§35), so the
    # channel has to exist for that promise to be testable at all.
    main._session_logger = RecordingLogger()
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

    def test_margin_leaves_the_book_alone_instead_of_erasing_it(self, executor):
        # None, not []: an empty list would be the statement "this bot holds nothing" and
        # would overwrite a stored book. A margin session has nothing to say about it —
        # those positions live at the venue (#209).
        store = SpyStore()

        _main(executor, store, persist=True)._persist_cold_start_carry_over()

        assert store.saves[0]['open_positions'] is None

    def test_a_spot_session_writes_the_book_it_holds(self, spot_executor):
        store = SpyStore()
        spot_executor.portfolio.restore_position_book([_note()])

        _main(spot_executor, store, persist=True)._persist_cold_start_carry_over()

        written = store.saves[0]['open_positions']
        assert [p.position_id for p in written] == ['pos_btcusd_47']
        assert written[0].entry_price == 61200.0

    def test_a_spot_session_with_nothing_open_says_so(self, spot_executor):
        # The empty LIST is a statement and must reach the store: it is how a closed
        # position stops being carried over.
        store = SpyStore()

        _main(spot_executor, store, persist=True)._persist_cold_start_carry_over()

        assert store.saves[0]['open_positions'] == []


class TestTheWriteNeverEndsTheSession:
    """A carry-over problem is a note in the session channel, never a stopped bot (§35)."""

    def test_a_failing_store_is_logged_and_swallowed(self, executor):
        main = _main(executor, SpyStore(fail=True), persist=True)

        main._persist_cold_start_carry_over()

        assert any('carry-over save failed' in message
                   for message in main._session_logger.errors)


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


class TestTheTickLoopSeam:
    """
    The seam that decides whether a pass writes the book (#355).

    Exercised through `__new__` for the same reason the rest of this file is: the method needs
    four attributes, and building a real tick loop would drag in a broker, a tick source and a
    decision logic without testing any of them.
    """

    @staticmethod
    def _loop(executor, writes, drift_ticks=500):
        """A tick loop carrying only what the book seam reads."""
        loop = AutotraderTickLoop.__new__(AutotraderTickLoop)
        loop._executor = executor
        loop._persist_position_book = lambda: writes.append(True) or True
        loop._book_watcher = PositionBookWatcher(executor.portfolio.get_open_positions())
        loop._book_drift_interval_ticks = drift_ticks
        loop._last_book_drift_tick = 0
        return loop

    def test_a_structural_change_writes_once_and_then_stays_quiet(self, spot_executor):
        writes = []
        loop = self._loop(spot_executor, writes)
        # The watcher is seeded on the book as it stood at construction, so the position
        # appearing afterwards is the change this pass sees.
        spot_executor.portfolio.restore_position_book([_note()])

        loop._persist_position_book_if_changed()
        loop._persist_position_book_if_changed()

        assert len(writes) == 1

    def test_a_failed_write_is_retried_on_the_next_pass(self, spot_executor):
        # The watcher must not advance on a write that did not go through: the change would
        # be reported once, to a caller that could not act on it, and the position would be
        # missing from the note until something else happened to move the book.
        attempts = []
        loop = self._loop(spot_executor, [])
        loop._persist_position_book = lambda: attempts.append(False) or False
        spot_executor.portfolio.restore_position_book([_note()])

        loop._persist_position_book_if_changed()
        loop._persist_position_book_if_changed()
        loop._persist_position_book_if_changed()

        assert len(attempts) == 3

    def test_a_moved_stop_waits_for_the_tick_cadence(self, spot_executor):
        # 11 ms per write on this tree, and a trailing stop moves on nearly every tick of a
        # trend — so the frequent half is bounded by ticks instead of firing immediately.
        writes = []
        loop = self._loop(spot_executor, writes, drift_ticks=500)
        spot_executor.portfolio.restore_position_book([_note()])
        loop._persist_position_book_if_changed(ticks_processed=1)
        assert len(writes) == 1

        spot_executor.get_open_positions()[0].stop_loss = 60000.0

        loop._persist_position_book_if_changed(ticks_processed=2)
        assert len(writes) == 1

        loop._persist_position_book_if_changed(ticks_processed=600)
        assert len(writes) == 2

    def test_the_cadence_needs_no_clock(self, spot_executor):
        # The first passes happen before the canonical clock is injected — a cadence that
        # asked for the time would raise ClockNotInjectedError inside the tick loop.
        writes = []
        loop = self._loop(spot_executor, writes)
        spot_executor.portfolio.restore_position_book([_note()])

        loop._persist_position_book_if_changed(ticks_processed=0)

        assert len(writes) == 1

    def test_nothing_wired_is_a_no_op(self, spot_executor):
        loop = self._loop(spot_executor, [])
        loop._persist_position_book = None

        loop._persist_position_book_if_changed()
