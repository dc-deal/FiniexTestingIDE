"""
Loop Cadence — The Seams Between the Event Loop's Units (#461)

The AutoTrader's event loop holds the loop itself; its trading days and its cadence work are units
of their own. Where one calls the other, what used to be one method's order is now a call across
a seam: a sealed trading day books its ledger rows and then writes the carry-over, and only a
write that succeeded moves what the carry-over is known to hold. The safety layer reads its trading
day from the booking period recorder it is handed, so its days roll with the booking periods'.
"""

from typing import List, Optional
from unittest.mock import MagicMock

from python.framework.autotrader.event_loop.autotrader_cadence_work import AutotraderCadenceWork
from python.framework.autotrader.event_loop.autotrader_trading_days import AutotraderTradingDays
from tests.shared.booking_day_harness import autotrader_loop, ticks_across_midnight


class _Booking:
    def get_sealed_periods(self) -> List[str]:
        return ['day 1']


class _Cadence:
    def __init__(self, steps: List[str]):
        self._steps = steps

    def write_carry_over(self) -> None:
        self._steps.append('carry_over')


class _BookWatcher:
    def __init__(self):
        self.accepted: Optional[list] = None

    def accept(self, positions: list) -> None:
        self.accepted = positions


class _Baseline:
    def get_baseline(self) -> str:
        return 'the baseline in memory'


def _cadence(write_succeeds: bool) -> AutotraderCadenceWork:
    cadence = AutotraderCadenceWork.__new__(AutotraderCadenceWork)
    cadence._persist_carry_over = lambda: write_succeeds
    cadence._executor = MagicMock()
    cadence._executor.portfolio.get_open_positions.return_value = ['the open book']
    cadence._book_watcher = _BookWatcher()
    cadence._risk_baseline = _Baseline()
    cadence._persisted_baseline = None
    return cadence


class TestASealedDay:

    def test_books_its_ledger_rows_then_writes_the_carry_over(self):
        """Killed between the two, the next session books the same number again — visibly."""
        steps: List[str] = []
        days = AutotraderTradingDays.__new__(AutotraderTradingDays)
        days._booking = _Booking()
        days._book_sealed_periods = lambda periods: steps.append(f'ledger {periods}')
        days._cadence = _Cadence(steps)

        days._book_sealed_day()

        assert steps == ["ledger ['day 1']", 'carry_over']


class TestTheCarryOverWrite:

    def test_a_write_that_succeeded_moves_the_book_and_the_baseline(self):
        cadence = _cadence(write_succeeds=True)

        cadence.write_carry_over()

        assert cadence._book_watcher.accepted == ['the open book']
        assert cadence._persisted_baseline == 'the baseline in memory'

    def test_a_write_that_failed_moves_nothing(self):
        cadence = _cadence(write_succeeds=False)

        cadence.write_carry_over()

        assert cadence._book_watcher.accepted is None
        assert cadence._persisted_baseline is None


class TestTheSafetyDay:

    def test_rolls_with_the_booking_periods_day(self):
        """Across midnight the breaker's daily records name the days the booking periods book."""
        loop = autotrader_loop(ticks_across_midnight(), spot_mode=False)
        loop.run()

        safety_days = [row.day for row in loop.get_safety_session().days]
        booked_days = [period.trading_day.isoformat() for period in loop.get_booking_periods()]
        assert safety_days == booked_days == ['2026-01-24', '2026-01-25']
