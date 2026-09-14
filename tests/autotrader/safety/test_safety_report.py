"""
Every risk figure names the baseline it was measured against (#356 Phase C / #314).

Two defects live in this area and the report exists for both.

The first is ambiguity. Four quantities in this codebase are called "initial", none of them
records when or at what price it was taken, and two are different numbers for the same
holdings — so a bare "-12 %" could not be traced to the denominator that produced it. The
report carries the baseline RECORD, whole.

The second is the shape of the question. The breaker's own state answers "is the bot blocked
right now", which is the wrong question at the end of a thirty-day run: a session that
touched 18 % at hour three and recovered ends looking exactly like one that never moved. So
the loop keeps running maxima and the report reads those.

Split the way the pipeline is (#391): the CAPTURE half is driven against the real tick-loop
methods bound to a stub, the DERIVE half against the real builder. Nothing here renders.
"""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import List, Optional

from python.framework.autotrader.autotrader_tick_loop import AutotraderTickLoop
from python.framework.autotrader.risk_baseline_tracker import RiskBaselineTracker
from python.framework.reporting.builders.safety_report_builder import (
    build_safety_report_from_session,
)
from python.framework.types.autotrader_types.autotrader_config_types import (
    AutoTraderConfig,
    SafetyConfig,
)
from python.framework.types.autotrader_types.safety_session_types import (
    SafetyDayRecord,
    SafetySessionRecord,
)
from python.framework.types.config_types.market_config_types import TradingModel
from python.framework.types.persistence_types import (
    BaselineKind,
    BaselineOrigin,
    RiskBaseline,
)

_DEPLOY = datetime(2026, 9, 10, 8, 0, tzinfo=timezone.utc)


class _NullLogger:
    """The observation path does not log; the block path logs and is not asserted here."""

    def verbose(self, m, *a, **k): pass
    def debug(self, m, *a, **k): pass
    def info(self, m, *a, **k): pass
    def warning(self, m, *a, **k): pass
    def error(self, m, *a, **k): pass


class _LoopStub:
    """
    The attributes the capture path reads and writes, and nothing else.

    Deliberately narrow, the same discipline as the flatten stub beside it: this stub IS the
    documented dependency surface, so a new attribute the path consults has to appear here
    or the test stops describing the production code.
    """

    def __init__(
        self,
        safety: SafetyConfig = None,
        trading_model: TradingModel = TradingModel.MARGIN,
        baseline: Optional[RiskBaseline] = None,
        now: datetime = _DEPLOY,
        open_positions: List[str] = None,
    ):
        self._config = AutoTraderConfig(safety=safety or SafetyConfig())
        self._trading_model = trading_model
        self._logger = _NullLogger()
        self._now = now
        self._risk_baseline = None
        if baseline is not None:
            self._risk_baseline = RiskBaselineTracker(
                mode=baseline.kind, restored=baseline, spot_mode=False,
                exclusive_account=False, clock_fn=lambda: self._now,
                logger=_NullLogger())

        self._safety_blocked = False
        self._safety_reason = ''
        self._safety_current_value = 0.0
        self._safety_drawdown_pct = 0.0
        self._safety_final_value = 0.0
        self._safety_worst_dd_abs = 0.0
        self._safety_worst_dd_abs_at = ''
        self._safety_worst_dd_pct = 0.0
        self._safety_worst_dd_pct_at = ''
        self._safety_block_count = 0
        self._safety_days: List[SafetyDayRecord] = []
        self._safety_current_day: Optional[str] = None
        self._day_baseline: Optional[RiskBaselineTracker] = None
        self._day_worst_loss_abs = 0.0
        self._day_worst_loss_pct = 0.0
        self._day_worst_loss_at = ''
        self._day_limit_hit = False
        self._flatten_sent_at_tick = None
        self._flatten_reason = ''
        self._flatten_completed = None
        self._flatten_unconfirmed: List[str] = []

        stub = self
        positions = [SimpleNamespace(position_id=p) for p in (open_positions or [])]

        class _Portfolio:
            @staticmethod
            def get_open_positions():
                return list(positions)

        class _Executor:
            portfolio = _Portfolio()

            @staticmethod
            def get_current_time_if_set():
                return stub._now

        self._executor = _Executor()

    def at(self, moment: datetime) -> '_LoopStub':
        """Move the canonical clock, so a stamp in a test is a chosen value."""
        self._now = moment
        return self

    def observe(self, value: float, baseline: float) -> None:
        AutotraderTickLoop._observe_safety_excursion(self, value, baseline)

    def check(self, value: float, baseline: float) -> None:
        AutotraderTickLoop._check_safety(self, value, baseline)

    def start_day(self, day: str, value: float) -> None:
        """Open a new daily baseline, as a tick crossing into a new UTC day does."""
        self._close_safety_day()
        self._safety_current_day = day
        self._day_baseline = RiskBaselineTracker(
            mode=BaselineKind.DAY_START, restored=None, spot_mode=False,
            exclusive_account=False, clock_fn=lambda: self._now, logger=_NullLogger())
        self._day_baseline.ensure_taken(value, origin=BaselineOrigin.DAY_BOUNDARY)

    def _close_safety_day(self) -> None:
        AutotraderTickLoop._close_safety_day(self)

    def _block(self, reason: str) -> None:
        AutotraderTickLoop._block(self, reason)

    def _check_daily_loss(self, current_value: float) -> None:
        AutotraderTickLoop._check_daily_loss(self, current_value)

    def capture(self) -> SafetySessionRecord:
        return AutotraderTickLoop.get_safety_session(self)


def _baseline(
    value: float = 10_000.0,
    kind: BaselineKind = BaselineKind.SESSION_FIXED,
    origin: BaselineOrigin = BaselineOrigin.FIRST_TICK,
) -> RiskBaseline:
    """A record a session is running against."""
    return RiskBaseline(kind=kind, value=value, taken_at_utc=_DEPLOY.isoformat(),
                        origin=origin)


class TestTheExcursionIsARunningMaximum:
    """
    The value at the end is not the session, and over thirty days that is the difference.

    A bot that drew down 18 % at hour three and recovered by hour four ends at −0 %. Read
    from a session-end snapshot it is indistinguishable from one that never moved — and the
    operator's decision after a thirty-day parity proof hangs on exactly that distinction.
    """

    def test_a_recovered_drawdown_is_still_reported(self):
        stub = _LoopStub(baseline=_baseline())

        stub.observe(8_200.0, 10_000.0)
        stub.observe(10_050.0, 10_000.0)

        record = stub.capture()
        assert record.worst_drawdown_abs == 1_800.0, (
            'the session-end value overwrote the low — a recovered 18 % excursion now reads '
            'exactly like a session that never moved')
        assert record.worst_drawdown_pct == 18.0
        assert record.final_value == 10_050.0, 'the end value is still reported, beside it'

    def test_a_gain_never_becomes_a_negative_drawdown(self):
        stub = _LoopStub(baseline=_baseline())

        stub.observe(11_000.0, 10_000.0)

        record = stub.capture()
        assert record.worst_drawdown_abs == 0.0
        assert record.worst_drawdown_pct == 0.0

    def test_it_is_measured_even_with_the_limits_switched_off(self):
        """
        The record of what WOULD have fired is the one a parity proof wants first.

        A session with `safety.enabled=false` still has a denominator and still moves against
        it; refusing to measure that would mean the operator has to arm a live limit in order
        to find out what it should be.
        """
        stub = _LoopStub(safety=SafetyConfig(enabled=False), baseline=_baseline())

        stub.observe(8_500.0, 10_000.0)

        record = stub.capture()
        assert record.enabled is False
        assert record.worst_drawdown_pct == 15.0

    def test_the_stamp_comes_from_the_canonical_clock(self):
        low = _DEPLOY + timedelta(hours=3)
        stub = _LoopStub(baseline=_baseline())

        stub.at(low).observe(9_000.0, 10_000.0)
        stub.at(low + timedelta(hours=1)).observe(10_000.0, 10_000.0)

        assert stub.capture().worst_drawdown_pct_at == low.isoformat(), (
            'the low carries the time it was OBSERVED at, not the time of the last tick')


class TestTheTwoExtremesCanBeTwoMoments:
    """
    A high-water-mark baseline moves, and then one figure always understates one limit.

    1 000 below a baseline of 10 000 is 10 %; 1 200 below a later peak of 20 000 is the
    larger amount and the smaller share. Tracking only the deepest amount would report 6 %
    as the session's worst percentage while `max_drawdown_pct` was breached at 10 %.
    """

    def test_the_deepest_amount_and_the_deepest_share_are_tracked_apart(self):
        first = _DEPLOY + timedelta(hours=1)
        second = _DEPLOY + timedelta(hours=5)
        stub = _LoopStub(baseline=_baseline(kind=BaselineKind.HIGH_WATER_MARK))

        stub.at(first).observe(9_000.0, 10_000.0)      # 1 000 = 10 %
        stub.at(second).observe(18_800.0, 20_000.0)    # 1 200 =  6 %

        record = stub.capture()
        assert record.worst_drawdown_abs == 1_200.0
        assert record.worst_drawdown_abs_at == second.isoformat()
        assert record.worst_drawdown_pct == 10.0, (
            'the deepest SHARE was overwritten by a larger amount against a bigger '
            'baseline — the percentage limit that actually fired is now invisible')
        assert record.worst_drawdown_pct_at == first.isoformat()

    def test_a_fixed_baseline_makes_them_the_same_moment(self):
        """The negative control, and it is the default configuration."""
        first = _DEPLOY + timedelta(hours=1)
        stub = _LoopStub(baseline=_baseline())

        stub.at(first).observe(9_000.0, 10_000.0)
        stub.at(first + timedelta(hours=2)).observe(9_500.0, 10_000.0)

        record = stub.capture()
        assert record.worst_drawdown_abs_at == record.worst_drawdown_pct_at == \
            first.isoformat()


class TestTheBlockCountsEngagements:
    """'Blocked on 40 000 ticks' is one event read as forty thousand."""

    def test_a_block_held_over_many_ticks_counts_once(self):
        stub = _LoopStub(safety=SafetyConfig(enabled=True, max_drawdown_pct=10.0),
                         baseline=_baseline())

        stub.check(8_000.0, 10_000.0)
        stub.check(8_100.0, 10_000.0)
        stub.check(8_200.0, 10_000.0)

        assert stub.capture().block_count == 1

    def test_a_block_that_cleared_and_returned_counts_twice(self):
        stub = _LoopStub(safety=SafetyConfig(enabled=True, max_drawdown_pct=10.0),
                         baseline=_baseline())

        stub.check(8_000.0, 10_000.0)
        stub.check(9_800.0, 10_000.0)
        stub.check(8_000.0, 10_000.0)

        record = stub.capture()
        assert record.block_count == 2
        assert record.blocked_at_end is True
        assert 'max_drawdown' in record.reason_at_end

    def test_a_session_that_ended_clear_says_so(self):
        stub = _LoopStub(safety=SafetyConfig(enabled=True, max_drawdown_pct=10.0),
                         baseline=_baseline())

        stub.check(8_000.0, 10_000.0)
        stub.check(9_900.0, 10_000.0)

        record = stub.capture()
        assert record.block_count == 1, 'it happened, and the session must still say so'
        assert record.blocked_at_end is False
        assert record.reason_at_end == ''


class TestOneRowPerDay:
    """
    A maximum over thirty days is a maximum over thirty different denominators.

    The daily limit is measured against a reference struck fresh every morning, so a single
    "worst daily loss" across a month is a number about nothing. One row per day, each
    naming its own baseline.
    """

    def test_a_day_boundary_files_the_day_that_ended(self):
        stub = _LoopStub(baseline=_baseline())
        stub.start_day('2026-09-10', 10_000.0)
        stub.observe(9_700.0, 10_000.0)

        stub.start_day('2026-09-11', 9_800.0)

        assert len(stub._safety_days) == 1
        assert stub._safety_days[0].day == '2026-09-10'
        assert stub._safety_days[0].worst_loss_abs == 300.0

    def test_the_new_day_starts_from_zero_again(self):
        stub = _LoopStub(baseline=_baseline())
        stub.start_day('2026-09-10', 10_000.0)
        stub.observe(9_700.0, 10_000.0)

        stub.start_day('2026-09-11', 9_800.0)
        stub.observe(9_750.0, 10_000.0)

        rows = stub.capture().days
        assert [row.worst_loss_abs for row in rows] == [300.0, 50.0], (
            'the day carried yesterday’s loss into today, which is the opposite of what '
            'daily means')

    def test_the_final_incomplete_day_is_filed_too(self):
        """It has no successor to close it — the capture does."""
        stub = _LoopStub(baseline=_baseline())
        stub.start_day('2026-09-10', 10_000.0)
        stub.observe(9_600.0, 10_000.0)

        record = stub.capture()

        assert [row.day for row in record.days] == ['2026-09-10']
        assert record.days[0].worst_loss_pct == 4.0

    def test_capturing_twice_does_not_file_the_day_twice(self):
        stub = _LoopStub(baseline=_baseline())
        stub.start_day('2026-09-10', 10_000.0)
        stub.observe(9_600.0, 10_000.0)

        stub.capture()

        assert len(stub.capture().days) == 1

    def test_a_day_that_tripped_its_own_limit_is_marked(self):
        stub = _LoopStub(
            safety=SafetyConfig(enabled=True, max_daily_loss_pct=2.0),
            baseline=_baseline())
        stub.start_day('2026-09-10', 10_000.0)

        stub.check(9_700.0, 10_000.0)

        record = stub.capture()
        assert record.days[0].limit_hit is True
        assert record.blocked_at_end is True

    def test_a_day_inside_its_limit_is_not(self):
        stub = _LoopStub(
            safety=SafetyConfig(enabled=True, max_daily_loss_pct=5.0),
            baseline=_baseline())
        stub.start_day('2026-09-10', 10_000.0)

        stub.check(9_700.0, 10_000.0)

        assert stub.capture().days[0].limit_hit is False


class TestAnUnresolvedHardStopIsNotReportedAsNeverFired:
    """
    The session can end for a reason of its own while the closes are still in flight.

    `flatten_completed=None` means the hard stop never fired. Leaving an unfinished drain at
    None would hide open positions at the venue behind a field that reads as "not
    applicable".
    """

    def test_a_drain_still_running_at_session_end_resolves_to_not_confirmed(self):
        stub = _LoopStub(baseline=_baseline(), open_positions=['pos_1'])
        stub._flatten_reason = 'hard drawdown 25.0% > 20.0%'
        stub._flatten_sent_at_tick = 40

        record = stub.capture()

        assert record.flatten_fired is True
        assert record.flatten_completed is False
        assert record.flatten_unconfirmed == ['pos_1']

    def test_a_hard_stop_that_never_fired_stays_none(self):
        """The negative control: None and False must not collapse into each other."""
        record = _LoopStub(baseline=_baseline(), open_positions=['pos_1']).capture()

        assert record.flatten_fired is False
        assert record.flatten_completed is None
        assert record.flatten_unconfirmed == []


class TestTheReportNamesItsDenominator:
    """The DERIVE half — what a reader gets, and what they can no longer be confused by."""

    def test_the_baseline_record_travels_whole(self):
        record = SafetySessionRecord(baseline=_baseline(), worst_drawdown_pct=12.0)

        report = build_safety_report_from_session('run1', record, SafetyConfig(), 'BTCUSD')

        assert report.baseline.kind is BaselineKind.SESSION_FIXED
        assert report.baseline.taken_at_utc == _DEPLOY.isoformat()
        assert report.baseline.origin is BaselineOrigin.FIRST_TICK
        assert report.baseline_value == 10_000.0, (
            'a consumer reading only the top level has no denominator at all')

    def test_a_carried_baseline_is_one_boolean_away_from_invisible(self):
        """The whole of #356, and nothing else on the record says it in one field."""
        record = SafetySessionRecord(
            baseline=_baseline(origin=BaselineOrigin.RESTORED_CARRY_OVER))

        report = build_safety_report_from_session('run1', record, SafetyConfig(), 'BTCUSD')

        assert report.baseline_restored is True

    def test_a_freshly_struck_baseline_is_not_flagged(self):
        record = SafetySessionRecord(baseline=_baseline())

        report = build_safety_report_from_session('run1', record, SafetyConfig(), 'BTCUSD')

        assert report.baseline_restored is False


class TestHowCloseItCame:
    """One figure, so the reader does not divide two numbers themselves."""

    def test_it_is_the_share_of_the_configured_limit(self):
        record = SafetySessionRecord(baseline=_baseline(), worst_drawdown_pct=8.0)

        report = build_safety_report_from_session(
            'run1', record, SafetyConfig(max_drawdown_pct=20.0), 'BTCUSD')

        assert report.soft_limit_used_pct == 40.0

    def test_no_limit_configured_answers_none_rather_than_zero(self):
        """'No limit' and 'nothing used of the limit' are different statements."""
        record = SafetySessionRecord(baseline=_baseline(), worst_drawdown_pct=8.0)

        report = build_safety_report_from_session('run1', record, SafetyConfig(), 'BTCUSD')

        assert report.soft_limit_used_pct is None
        assert report.hard_limit_used_pct is None

    def test_the_nearer_of_the_two_limits_wins(self):
        """
        The percentage and the absolute threshold are independent, either can fire first.

        Reporting only the percentage would understate a session where the absolute floor
        was the binding constraint — which is the account the absolute limit exists for.
        """
        record = SafetySessionRecord(
            baseline=_baseline(), worst_drawdown_pct=5.0, worst_drawdown_abs=500.0)

        report = build_safety_report_from_session(
            'run1', record,
            SafetyConfig(max_drawdown_pct=50.0, max_drawdown_abs=600.0), 'BTCUSD')

        assert report.soft_limit_used_pct == 500.0 / 600.0 * 100.0, (
            'the absolute limit was 83 % used while the percentage one was 10 % used — the '
            'report showed the comfortable half')

    def test_the_hard_threshold_is_measured_separately(self):
        record = SafetySessionRecord(baseline=_baseline(), worst_drawdown_pct=9.0)

        report = build_safety_report_from_session(
            'run1', record,
            SafetyConfig(max_drawdown_pct=18.0, max_drawdown_pct_hard=30.0), 'BTCUSD')

        assert report.soft_limit_used_pct == 50.0
        assert report.hard_limit_used_pct == 30.0


class TestTheFloorIsOneQuantityWithTwoSpellings:
    """
    Since #356 `min_balance` and `min_equity` denominate the same thing: the account value.

    Only the account model decides which key a profile writes. The report names the key so
    that one limit cannot be read as two different ones — the report is where a reader meets
    both names side by side.
    """

    def test_spot_reports_the_key_a_spot_profile_writes(self):
        record = SafetySessionRecord(baseline=_baseline(), spot_mode=True)

        report = build_safety_report_from_session(
            'run1', record, SafetyConfig(min_equity=5_000.0, min_balance=1.0), 'BTCUSD')

        assert report.limits.min_floor_key == 'min_equity'
        assert report.limits.min_floor == 5_000.0

    def test_margin_reports_the_other_one(self):
        record = SafetySessionRecord(baseline=_baseline(), spot_mode=False)

        report = build_safety_report_from_session(
            'run1', record, SafetyConfig(min_equity=1.0, min_balance=5_000.0), 'EURUSD')

        assert report.limits.min_floor_key == 'min_balance'
        assert report.limits.min_floor == 5_000.0


class TestTheWorstDayIsChosenInTheBuilder:
    """
    A renderer that picks the maximum out of thirty rows has built its own aggregate.

    Thirty rows do not belong on a console, so something has to choose — and when a renderer
    chooses, the console and the API answer the same question differently (#391).
    """

    def test_it_names_the_day_and_not_only_the_number(self):
        record = SafetySessionRecord(baseline=_baseline(), days=[
            SafetyDayRecord(day='2026-09-10', worst_loss_abs=90.0, worst_loss_pct=0.9),
            SafetyDayRecord(day='2026-09-11', worst_loss_abs=310.0, worst_loss_pct=3.1),
            SafetyDayRecord(day='2026-09-12', worst_loss_abs=120.0, worst_loss_pct=1.2),
        ])

        report = build_safety_report_from_session('run1', record, SafetyConfig(), 'BTCUSD')

        assert report.worst_day == '2026-09-11'
        assert report.worst_day_loss_abs == 310.0
        assert report.worst_day_loss_pct == 3.1

    def test_the_days_that_hit_a_limit_are_counted(self):
        record = SafetySessionRecord(baseline=_baseline(), days=[
            SafetyDayRecord(day='2026-09-10', limit_hit=True),
            SafetyDayRecord(day='2026-09-11'),
            SafetyDayRecord(day='2026-09-12', limit_hit=True),
        ])

        report = build_safety_report_from_session('run1', record, SafetyConfig(), 'BTCUSD')

        assert report.days_limit_hit == 2

    def test_a_session_shorter_than_a_day_has_no_worst_day(self):
        report = build_safety_report_from_session(
            'run1', SafetySessionRecord(baseline=_baseline()), SafetyConfig(), 'BTCUSD')

        assert report.worst_day == ''
        assert report.days == []
