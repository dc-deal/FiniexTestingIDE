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

from datetime import datetime, timezone
from types import SimpleNamespace
from typing import List, Optional, Set

import pytest

from python.framework.autotrader.autotrader_main import AutotraderMain
from python.framework.autotrader.autotrader_tick_loop import AutotraderTickLoop
from python.framework.autotrader.cold_start_setup import ColdStartSetup, setup_cold_start
from python.framework.autotrader.risk_baseline_tracker import RiskBaselineTracker
from python.framework.persistence.position_book_watcher import PositionBookWatcher
from python.framework.types.autotrader_types.autotrader_config_types import SafetyConfig
from python.framework.types.persistence_types import (
    AccountDrawdownCarryOver,
    BaselineKind,
    ColdStartPayload,
    PositionCarryOver,
    RiskBaseline,
)
from tests.autotrader.cold_start.conftest import RecordingLogger

_CLOCK = datetime(2026, 9, 14, 8, 0, tzinfo=timezone.utc)


class SpyStore:
    """Records what would be persisted."""

    def __init__(self, fail: bool = False):
        self.saves: List[dict] = []
        self._fail = fail

    def save(self, session_key: str, highest_position_counter: int,
             highest_segment_no: int = 0,
             keys_in_use: Optional[Set[str]] = None,
             open_positions: Optional[List[PositionCarryOver]] = None,
             risk_baseline: Optional[RiskBaseline] = None,
             account_drawdown: Optional[AccountDrawdownCarryOver] = None,
             deployment_id: Optional[str] = None,
             refresh_index: bool = True) -> None:
        if self._fail:
            raise OSError('disk full')
        self.saves.append({
            'session_key': session_key,
            'highest_position_counter': highest_position_counter,
            'highest_segment_no': highest_segment_no,
            'keys_in_use': set(keys_in_use or ()),
            'open_positions': open_positions,
            'risk_baseline': risk_baseline,
            'account_drawdown': account_drawdown,
            'deployment_id': deployment_id,
            'refresh_index': refresh_index,
        })

    def load(self):
        """A spy has no stored predecessor — the baseline restore reads an empty payload."""
        return ColdStartPayload()


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


def _main(executor, store, persist: bool, keys_in_use=None,
          risk_baseline=None, persist_baseline: bool = True,
          persist_venue_claims: bool = True) -> AutotraderMain:
    """
    A session object carrying only what the carry-over write reads.

    The list is the point: this helper IS the write's dependency surface, so a new
    attribute the write consults has to appear here or the test stops describing it.

    Args:
        executor: The session's executor
        store: The carry-over store (or a spy)
        persist: Whether this session may write at all
        keys_in_use: Session halves the venue currently shows
        risk_baseline: The baseline tracker (#356), or None for a session that never got
            one — every abort before the cold start decided still reaches the write
        persist_baseline: The switch that governs BOTH carried risk records — the
            denominator (#356) and the reported drawdown curve (#497)
        persist_venue_claims: Whether the half that CLAIMS SOMETHING ABOUT THE VENUE — the
            session key and the open book — may be written. False for a dry run
    """
    main = AutotraderMain.__new__(AutotraderMain)
    main._executor = executor
    # Only `.safety` is read here, so the stand-in carries only that — the same
    # duck-typing the spy store and the recording logger in this file already use.
    main._config = SimpleNamespace(
        safety=SafetyConfig(persist_baseline=persist_baseline))
    # Minted at the first session of a deployment and carried by every successor; the
    # write puts it on every carry-over so a session that dies early still leaves its
    # membership behind.
    main._deployment_id = 'deploy_20260914_080000'
    # The write promises to swallow its own failures into the session channel (§35), so the
    # channel has to exist for that promise to be testable at all.
    main._session_logger = RecordingLogger()
    main._risk_baseline = risk_baseline
    # None, because the write happens at BOOT too — before the loop exists. The carry-over
    # reads the booking-period high-water mark from it (#537), and the store treats the value
    # as a floor, so a zero here leaves a deployment's period count alone.
    main._tick_loop = None
    main._cold_start = ColdStartSetup(
        proceed=True,
        store=store,
        persist=persist,
        persist_venue_claims=persist_venue_claims,
        keys_in_use=set(keys_in_use or ()),
    )
    return main


class TestNothingIsWrittenWithoutPermission:
    """The flag is set only after adoption has actually gone through."""

    def test_a_refused_boot_writes_nothing(self, executor):
        """
        Not one field. A restart loop feeds on itself otherwise: the refusal grades the run
        non-zero, a supervisor relaunches, and each boot consumes its own key window.
        """
        store = SpyStore()

        _main(executor, store, persist=False)._persist_cold_start_carry_over()

        assert store.saves == []

    def test_no_store_is_a_no_op(self, executor):
        # A Field Study session, or cold_start disabled: no store was ever built.
        _main(executor, None, persist=True)._persist_cold_start_carry_over()


class TestADryRunWritesOnlyWhatItCanClaim:
    """
    The payload has two halves and a dry run may write one of them (#355/#497).

    The session key and the open position book are CLAIMS ABOUT THE VENUE — this key sent
    orders, this book is open. A dry run sent nothing anywhere, so a successor inheriting
    either would believe in orders and positions that do not exist. The risk baseline, the
    reported drawdown curve and the deployment identity are OUR OWN records: numbers this
    process computed, true whether or not the venue was real.

    Before the split the whole write was refused, which had a consequence nobody wanted: a
    MOCK session is a dry run by definition (`_is_dry_run` answers on the adapter type), so
    the deployment identity and the drawdown curve could not be exercised at all without a
    real venue.
    """

    def test_it_claims_no_session_key_and_no_book(self, spot_executor):
        store = SpyStore()

        _main(spot_executor, store, persist=True,
              keys_in_use={'8b3f'},
              persist_venue_claims=False)._persist_cold_start_carry_over()

        assert len(store.saves) == 1
        written = store.saves[0]
        assert written['session_key'] == '', 'a dry run recorded a key it never sent orders under'
        assert written['keys_in_use'] == set(), 'a dry run protected a key it never used'
        assert written['open_positions'] is None, (
            'the successor would inherit a book the venue does not hold')

    def test_it_still_carries_its_own_records(self, executor):
        """The half that is ours: the curve, the denominator and the deployment identity."""
        store = SpyStore()
        tracker = RiskBaselineTracker(
            mode=BaselineKind.SESSION_FIXED, restored=None, spot_mode=False,
            exclusive_account=True, clock_fn=lambda: _CLOCK, logger=RecordingLogger())
        tracker.ensure_taken(10_000.0)

        _main(executor, store, persist=True, risk_baseline=tracker,
              persist_venue_claims=False)._persist_cold_start_carry_over()

        written = store.saves[0]
        assert written['risk_baseline'] is not None
        assert written['deployment_id'] == 'deploy_20260914_080000'


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


class TestTheReportedDrawdownReachesTheStore:
    """
    The curve is written on the same points as everything else here (#497).

    It has no cadence of its own on purpose: one write plus its index rebuild costs tens of
    milliseconds on this tree (§42) and has no business in a tick loop. So the boot write,
    the shutdown write and every structural change of the open book carry it — and if the
    wiring drops it, a thirty-day run reports the drawdown of its last segment while every
    unit test stays green.
    """

    def test_a_measured_curve_is_written_beside_the_book(self, executor):
        store = SpyStore()
        executor.portfolio.sample_equity()

        _main(executor, store, persist=True)._persist_cold_start_carry_over()

        carried = store.saves[0]['account_drawdown']
        assert carried is not None, (
            'the successor starts its curve at its own opening balance and a month of '
            'drawdown is gone')
        assert carried.max_equity == pytest.approx(
            executor.portfolio.get_portfolio_statistics().max_equity)

    def test_the_boot_write_carries_nothing_before_the_first_sample(self, executor):
        """
        The boot write happens BEFORE the first tick, and that is the trap.

        At that moment the peak is only the opening balance. Written as a measurement it
        would hand the successor a high nobody reached — and a funded account opening below
        it would report a drawdown that never happened. None is what the store reads as
        "not supplied", so the predecessor's real record survives the boot instead of being
        replaced by a placeholder.
        """
        store = SpyStore()

        _main(executor, store, persist=True)._persist_cold_start_carry_over()

        assert store.saves[0]['account_drawdown'] is None

    def test_the_switch_withholds_it(self, executor):
        """
        `persist_baseline=false` is the deliberate escape for a fresh reference per start.

        It governs BOTH records rather than only the denominator: a session where one
        survives and the other does not produces two drawdown figures that silently describe
        different periods, which is the confusion #497 exists to end.
        """
        store = SpyStore()
        executor.portfolio.sample_equity()

        _main(executor, store, persist=True,
              persist_baseline=False)._persist_cold_start_carry_over()

        assert store.saves[0]['account_drawdown'] is None


class TestTheDeploymentIdentityReachesTheStore:
    """
    The join key that turns N session records into one history (#497).

    It rides EVERY carry-over write rather than only the boot one, and the reason is the
    failure this store exists for: a session killed hard (SIGKILL, OOM, power) before its
    first structural write would otherwise leave a ledger row with no membership, and nothing
    afterwards can say which deployment it belonged to. The profile name cannot stand in — the
    same bot stopped for a month and restarted is a second deployment, and from outside the
    two are identical.
    """

    def test_every_write_carries_it(self, executor):
        store = SpyStore()

        _main(executor, store, persist=True)._persist_cold_start_carry_over()

        assert store.saves[0]['deployment_id'] == 'deploy_20260914_080000', (
            'a session that dies before its first structural write leaves a ledger row that '
            'can never be attached to its deployment')


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
    The seam that decides whether a pass writes the carry-over (#355 / #356).

    Exercised through `__new__` for the same reason the rest of this file is: the method needs
    four attributes, and building a real tick loop would drag in a broker, a tick source and a
    decision logic without testing any of them.
    """

    @staticmethod
    def _loop(executor, writes, drift_ticks=500, risk_baseline=None):
        """A tick loop carrying only what the carry-over seam reads."""
        loop = AutotraderTickLoop.__new__(AutotraderTickLoop)
        loop._executor = executor
        loop._persist_carry_over = lambda: writes.append(True) or True
        loop._book_watcher = PositionBookWatcher(executor.portfolio.get_open_positions())
        loop._book_drift_interval_ticks = drift_ticks
        loop._last_book_drift_tick = 0
        # #356 — the baseline half of the same document. None/None is "no tracker", which is
        # every case in this class except the baseline tests below.
        loop._risk_baseline = risk_baseline
        loop._persisted_baseline = (
            risk_baseline.get_baseline() if risk_baseline is not None else None)
        return loop

    def test_a_structural_change_writes_once_and_then_stays_quiet(self, spot_executor):
        writes = []
        loop = self._loop(spot_executor, writes)
        # The watcher is seeded on the book as it stood at construction, so the position
        # appearing afterwards is the change this pass sees.
        spot_executor.portfolio.restore_position_book([_note()])

        loop._persist_carry_over_if_needed()
        loop._persist_carry_over_if_needed()

        assert len(writes) == 1

    def test_a_failed_write_is_retried_on_the_next_pass(self, spot_executor):
        # The watcher must not advance on a write that did not go through: the change would
        # be reported once, to a caller that could not act on it, and the position would be
        # missing from the note until something else happened to move the book.
        attempts = []
        loop = self._loop(spot_executor, [])
        loop._persist_carry_over = lambda: attempts.append(False) or False
        spot_executor.portfolio.restore_position_book([_note()])

        loop._persist_carry_over_if_needed()
        loop._persist_carry_over_if_needed()
        loop._persist_carry_over_if_needed()

        assert len(attempts) == 3

    def test_a_moved_stop_waits_for_the_tick_cadence(self, spot_executor):
        # 11 ms per write on this tree, and a trailing stop moves on nearly every tick of a
        # trend — so the frequent half is bounded by ticks instead of firing immediately.
        writes = []
        loop = self._loop(spot_executor, writes, drift_ticks=500)
        spot_executor.portfolio.restore_position_book([_note()])
        loop._persist_carry_over_if_needed(ticks_processed=1)
        assert len(writes) == 1

        spot_executor.get_open_positions()[0].stop_loss = 60000.0

        loop._persist_carry_over_if_needed(ticks_processed=2)
        assert len(writes) == 1

        loop._persist_carry_over_if_needed(ticks_processed=600)
        assert len(writes) == 2

    def test_the_cadence_needs_no_clock(self, spot_executor):
        # The first passes happen before the canonical clock is injected — a cadence that
        # asked for the time would raise ClockNotInjectedError inside the tick loop.
        writes = []
        loop = self._loop(spot_executor, writes)
        spot_executor.portfolio.restore_position_book([_note()])

        loop._persist_carry_over_if_needed(ticks_processed=0)

        assert len(writes) == 1

    def test_nothing_wired_is_a_no_op(self, spot_executor):
        loop = self._loop(spot_executor, [])
        loop._persist_carry_over = None

        loop._persist_carry_over_if_needed()


class TestTheBaselineReachesTheDiskBeforeAHardKill:
    """
    The one write the position book cannot carry (#356).

    The carry-over used to be written in-run only when the open BOOK changed structurally,
    and only in spot mode. That left three sessions writing their risk baseline for the
    first time at SHUTDOWN: a margin session (no book is written at all), a spot session
    that never opens a position, and any session killed before its first trade. A hard kill
    — OOM, power, `kill -9` — is precisely the moment a shutdown write does not happen, and
    the successor then re-anchors its baseline at the drawn-down value. That is the drift
    #356 exists to remove, surviving in the case it was written for.

    The boot write cannot cover it either: it runs BEFORE the first tick, when no baseline
    has been taken yet.
    """

    @staticmethod
    def _tracker(value: float, kind: BaselineKind = BaselineKind.SESSION_FIXED):
        """A tracker that has already taken its baseline, as the first tick leaves it."""
        tracker = RiskBaselineTracker(
            mode=kind, restored=None, spot_mode=False, exclusive_account=False,
            clock_fn=lambda: _CLOCK, logger=RecordingLogger())
        tracker.ensure_taken(value)
        return tracker

    @staticmethod
    def _loop(executor, writes, tracker, drift_ticks=500, seeded=None):
        """A tick loop whose book is quiet, so only the baseline can trigger a write."""
        loop = AutotraderTickLoop.__new__(AutotraderTickLoop)
        loop._executor = executor
        loop._persist_carry_over = lambda: writes.append(True) or True
        loop._book_watcher = PositionBookWatcher(executor.portfolio.get_open_positions())
        loop._book_drift_interval_ticks = drift_ticks
        loop._last_book_drift_tick = 0
        loop._risk_baseline = tracker
        loop._persisted_baseline = seeded
        return loop

    def test_a_baseline_taken_this_session_is_written_without_a_book_change(
            self, spot_executor):
        writes = []
        loop = self._loop(spot_executor, writes, self._tracker(10_000.0))

        loop._persist_carry_over_if_needed(ticks_processed=1)

        assert len(writes) == 1, (
            'the baseline reaches the disk for the first time at shutdown — a hard kill '
            'loses it, and the successor re-anchors at the drawn-down value')

    def test_and_then_it_stays_quiet(self, spot_executor):
        """A fixed baseline never changes again, so one write is the whole cost."""
        writes = []
        loop = self._loop(spot_executor, writes, self._tracker(10_000.0))

        loop._persist_carry_over_if_needed(ticks_processed=1)
        loop._persist_carry_over_if_needed(ticks_processed=2)
        loop._persist_carry_over_if_needed(ticks_processed=600)

        assert len(writes) == 1

    def test_a_restored_baseline_is_not_written_again(self, spot_executor):
        """
        It came FROM the disk and the boot write already carried it.

        Seeded at construction rather than assumed: a tracker holding a record before the
        first tick restored it, and re-writing it would spend 11 ms proving what is already
        on disk.
        """
        tracker = self._tracker(10_000.0)
        writes = []
        loop = self._loop(spot_executor, writes, tracker,
                          seeded=tracker.get_baseline())

        loop._persist_carry_over_if_needed(ticks_processed=1)

        assert writes == []

    def test_a_new_peak_waits_for_the_cadence(self, spot_executor):
        """
        A high-water mark advances on a great many ticks of a trend, at 11 ms a write.

        Writing a peak late is safe in a way writing the FIRST baseline late is not: a
        stale, lower peak is a looser limit, never a tighter one.
        """
        tracker = self._tracker(10_000.0, BaselineKind.HIGH_WATER_MARK)
        writes = []
        loop = self._loop(spot_executor, writes, tracker, drift_ticks=500,
                          seeded=tracker.get_baseline())

        tracker.observe(10_500.0)
        loop._persist_carry_over_if_needed(ticks_processed=2)
        assert writes == [], 'a write per new peak, on every tick of a trend'

        loop._persist_carry_over_if_needed(ticks_processed=600)
        assert len(writes) == 1

    def test_a_session_without_a_tracker_is_unaffected(self, spot_executor):
        """The negative control — nothing here may start writing on its own."""
        writes = []
        loop = self._loop(spot_executor, writes, None)

        loop._persist_carry_over_if_needed(ticks_processed=1)

        assert writes == []
