"""
The risk baseline survives a restart, and says which denominator it is (#356).

The defect, in two lines:

    deploy: equity 10 000  ->  baseline 10 000
    drawdown to 9 200 (-8 %)  ->  RESTART  ->  baseline 9 200 (-0 %)

The baseline was captured on the first tick and held in memory, so a restart mid-drawdown
re-anchored it at the already drawn-down value. The safety margin followed the bot down and
the accumulated loss was forgotten — silently, because a fresh session at -0 % looks exactly
like a healthy one. A thirty-day unattended run will restart.

The fix is a RECORD rather than a float, carried through the cold-start store. Its invariant
is the bug written the other way round: a restored baseline keeps its ORIGINAL stamp and
says `origin=restored_carry_over`, so nothing can re-stamp it as new.

The tracker decides nothing about breaching — the circuit breaker reads the value and
compares. What is under test here is only the denominator.
"""

from datetime import datetime, timedelta, timezone
from typing import List, Optional

import pytest

from python.framework.autotrader.risk_baseline_tracker import RiskBaselineTracker
from python.framework.types.persistence_types import (
    BaselineKind,
    BaselineOrigin,
    BaselineQuantities,
    RiskBaseline,
)

_DEPLOY = datetime(2026, 9, 10, 8, 0, tzinfo=timezone.utc)
_LATER = _DEPLOY + timedelta(days=3)


class _RecordingLogger:
    """Captures what reached the operator, so 'was it said?' is directly assertable."""

    def __init__(self):
        self.infos: List[str] = []
        self.warnings: List[str] = []
        self.errors: List[str] = []

    def verbose(self, message: str, *a, **k): pass
    def debug(self, message: str, *a, **k): pass
    def info(self, message: str, *a, **k): self.infos.append(message)
    def warning(self, message: str, *a, **k): self.warnings.append(message)
    def error(self, message: str, *a, **k): self.errors.append(message)


def _tracker(
    mode: BaselineKind = BaselineKind.SESSION_FIXED,
    restored: Optional[RiskBaseline] = None,
    spot_mode: bool = False,
    now: datetime = _LATER,
    exclusive_account: bool = False,
) -> RiskBaselineTracker:
    """A tracker on a fixed clock, so every stamp in a test is a chosen value."""
    return RiskBaselineTracker(
        mode=mode, restored=restored, spot_mode=spot_mode,
        exclusive_account=exclusive_account,
        clock_fn=lambda: now, logger=_RecordingLogger())


def _record(
    value: float = 10_000.0,
    kind: BaselineKind = BaselineKind.SESSION_FIXED,
    taken_at: datetime = _DEPLOY,
) -> RiskBaseline:
    """The note a previous session left."""
    return RiskBaseline(
        kind=kind, value=value, taken_at_utc=taken_at.isoformat(),
        origin=BaselineOrigin.FIRST_TICK)


class TestTheRestartDrift:
    """The issue, as three assertions."""

    def test_a_restored_baseline_is_not_re_anchored_by_the_first_tick(self):
        tracker = _tracker(restored=_record(10_000.0))

        # The successor's first tick, mid-drawdown.
        tracker.ensure_taken(9_200.0)

        assert tracker.get_value() == 10_000.0, (
            'the baseline followed the bot down — the successor starts at -0 % against a '
            'lower reference, which is not a stable margin')

    def test_and_it_keeps_the_original_stamp(self):
        """
        Re-stamping is the drift wearing the shape of a fresh start.

        The stamp says when the denominator was STRUCK. A restart does not strike a new one,
        and a drawdown-duration figure reads this field.
        """
        tracker = _tracker(restored=_record(taken_at=_DEPLOY))

        baseline = tracker.get_baseline()
        assert baseline.taken_at_utc == _DEPLOY.isoformat()
        assert baseline.origin is BaselineOrigin.RESTORED_CARRY_OVER, (
            'a reader cannot tell a carried baseline from a fresh one without this')

    def test_a_first_run_takes_its_own(self):
        """The negative control: with no predecessor the first tick is the right moment."""
        tracker = _tracker()

        tracker.ensure_taken(9_200.0)

        assert tracker.get_value() == 9_200.0
        assert tracker.get_baseline().origin is BaselineOrigin.FIRST_TICK


class TestTakingItIsIdempotent:
    """`ensure_taken` runs on every tick; only the first one may do anything."""

    def test_the_second_tick_changes_nothing(self):
        tracker = _tracker()
        tracker.ensure_taken(10_000.0)

        tracker.ensure_taken(9_000.0)
        tracker.ensure_taken(11_000.0)

        assert tracker.get_value() == 10_000.0

    def test_no_baseline_reads_as_zero_rather_than_a_guess(self):
        """Zero is what the breaker already reads as 'no drawdown check'."""
        tracker = _tracker()

        assert tracker.get_baseline() is None
        assert tracker.get_value() == 0.0


class TestTheHighWaterMark:
    """Trails the peak, never the trough — and every advance carries its own date."""

    def test_it_rises_with_a_new_peak(self):
        tracker = _tracker(mode=BaselineKind.HIGH_WATER_MARK)
        tracker.ensure_taken(10_000.0)

        tracker.observe(10_500.0)

        assert tracker.get_value() == 10_500.0
        assert tracker.get_baseline().origin is BaselineOrigin.HWM_UPDATE

    def test_it_does_not_fall_with_a_drawdown(self):
        tracker = _tracker(mode=BaselineKind.HIGH_WATER_MARK)
        tracker.ensure_taken(10_000.0)
        tracker.observe(10_500.0)

        tracker.observe(9_000.0)

        assert tracker.get_value() == 10_500.0, (
            'a high-water mark that falls is not a high-water mark, and the drawdown it '
            'measures would reset itself at the worst possible moment')

    def test_a_fixed_baseline_ignores_the_peak_entirely(self):
        tracker = _tracker(mode=BaselineKind.SESSION_FIXED)
        tracker.ensure_taken(10_000.0)

        tracker.observe(12_000.0)

        assert tracker.get_value() == 10_000.0, 'that is what fixed means'

    def test_the_advance_carries_its_own_date(self):
        """A peak without its date cannot answer how long the drawdown has lasted."""
        tracker = _tracker(mode=BaselineKind.HIGH_WATER_MARK,
                           restored=_record(10_000.0, BaselineKind.HIGH_WATER_MARK))

        tracker.observe(10_500.0)

        assert tracker.get_baseline().taken_at_utc == _LATER.isoformat(), (
            'the new peak still carries the old peak’s date')


class TestASpotRecordCanBeReDerived:
    """A denominator nobody can check is a denominator nobody can argue with."""

    def test_value_equals_quote_plus_base_times_mark(self):
        tracker = _tracker(spot_mode=True)

        tracker.ensure_taken(
            10_000.0, mark_price=50_000.0,
            quantities=BaselineQuantities(quote=5_000.0, base=0.1))

        baseline = tracker.get_baseline()
        assert baseline.quantities.quote + baseline.quantities.base * baseline.mark_price \
            == pytest.approx(baseline.value)

    def test_a_margin_record_carries_no_price(self):
        """
        A cash-denominated account has no price at which its value was struck.

        Market practice records none either; inventing one would be a field that reads as
        meaningful and is not.
        """
        tracker = _tracker(spot_mode=False)

        tracker.ensure_taken(
            10_000.0, mark_price=50_000.0,
            quantities=BaselineQuantities(quote=5_000.0, base=0.1))

        baseline = tracker.get_baseline()
        assert baseline.mark_price is None
        assert baseline.quantities is None


class TestTheOperatorIsTold:
    """A denominator that changed silently is the one nobody checks."""

    def test_a_restore_is_said_out_loud(self):
        logger = _RecordingLogger()
        RiskBaselineTracker(
            mode=BaselineKind.SESSION_FIXED, restored=_record(), spot_mode=False,
            exclusive_account=False, clock_fn=lambda: _LATER, logger=logger)

        assert any('restored' in m.lower() for m in logger.infos)

    def test_a_mode_change_between_runs_is_a_warning_and_the_record_wins(self):
        """
        Converting it silently would change what every limit measures.

        The operator edited the profile between runs; the honest answer is to keep the
        record that exists and say which one is in force.
        """
        logger = _RecordingLogger()
        tracker = RiskBaselineTracker(
            mode=BaselineKind.HIGH_WATER_MARK,
            restored=_record(10_000.0, BaselineKind.SESSION_FIXED),
            spot_mode=False, exclusive_account=False,
            clock_fn=lambda: _LATER, logger=logger)

        assert tracker.get_baseline().kind is BaselineKind.SESSION_FIXED
        assert any('session_fixed' in m for m in logger.warnings)


class TestTheExclusivityDeclarationTravelsWithIt:
    """
    The denominator is the bot's own only if the account is the bot's alone (#489).

    A drawdown percentage shown without that premise is a number about somebody else's
    deposits too.
    """

    def test_it_is_stamped_onto_the_record(self):
        tracker = _tracker(exclusive_account=True)

        tracker.ensure_taken(10_000.0)

        assert tracker.get_baseline().exclusive_account is True


class TestARestoredKindGovernsTheBehaviourToo:
    """
    Keeping the record's label while running the configured mode converts it one step later.

    A `session_fixed` record restored into a session configured for `high_water_mark` would
    advance on the first new peak and be written back as a high-water mark — the silent
    conversion the warning promises does not happen, arriving on the second tick instead of
    the first.
    """

    def test_a_restored_fixed_baseline_does_not_start_trailing(self):
        tracker = _tracker(mode=BaselineKind.HIGH_WATER_MARK,
                           restored=_record(10_000.0, BaselineKind.SESSION_FIXED))

        tracker.observe(12_000.0)

        assert tracker.get_value() == 10_000.0, (
            'the restored fixed baseline started trailing the peak — every limit now means '
            'something the operator did not ask for')
        assert tracker.get_baseline().kind is BaselineKind.SESSION_FIXED

    def test_a_restored_hwm_keeps_trailing_under_a_fixed_config(self):
        """The same rule in the other direction — the record wins, both ways."""
        tracker = _tracker(mode=BaselineKind.SESSION_FIXED,
                           restored=_record(10_000.0, BaselineKind.HIGH_WATER_MARK))

        tracker.observe(12_000.0)

        assert tracker.get_value() == 12_000.0
        assert tracker.get_baseline().kind is BaselineKind.HIGH_WATER_MARK
