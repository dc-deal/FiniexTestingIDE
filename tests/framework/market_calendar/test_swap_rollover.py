"""
Swap-rollover + DST calendar tests (#365).

Covers the MarketCalendar swap-rollover helpers and the time_utils DST conversion the
overnight-swap accrual relies on: MT5→Python weekday mapping, DST-aware local→UTC,
rollover-boundary enumeration (weekend-skip + triple weekday), next-rollover, and
next-market-close. Pure functions — no executor.
"""

from datetime import date, datetime, timezone

from python.framework.utils.market_calendar import MarketCalendar
from python.framework.utils.time_utils import local_time_to_utc, mt5_weekday_to_python

NY = 'America/New_York'


def _utc(year, month, day, hour, minute=0):
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


# --- MT5 → Python weekday mapping (MT5 Sun=0; Python Mon=0) ---

def test_mt5_weekday_mapping():
    assert mt5_weekday_to_python(3) == 2   # Wed (the common triple day)
    assert mt5_weekday_to_python(1) == 0   # Mon
    assert mt5_weekday_to_python(0) == 6   # Sun
    assert mt5_weekday_to_python(5) == 4   # Fri


# --- DST-aware local → UTC (17:00 New York) ---

def test_local_to_utc_winter_est():
    # Winter (EST, UTC-5): 17:00 NY = 22:00 UTC
    assert local_time_to_utc(date(2026, 1, 14), '17:00', NY) == _utc(2026, 1, 14, 22)


def test_local_to_utc_summer_edt():
    # Summer (EDT, UTC-4): 17:00 NY = 21:00 UTC
    assert local_time_to_utc(date(2026, 7, 15), '17:00', NY) == _utc(2026, 7, 15, 21)


# --- iter_swap_rollovers (window is (start, end]) ---

def test_iter_two_normal_nights():
    # Mon 10:00 → Wed 09:00 : Mon + Tue rollovers (Wed 22:00 not yet reached), no triple
    rollovers = MarketCalendar.iter_swap_rollovers(
        _utc(2026, 1, 12, 10), _utc(2026, 1, 14, 9), '17:00', NY, 2)
    assert [(r.weekday(), m) for r, m in rollovers] == [(0, 1), (1, 1)]


def test_iter_triple_wednesday():
    # Tue 12:00 → Thu 08:00 : Tue ×1 + Wed ×3 (triple) = 4 swap-days
    rollovers = MarketCalendar.iter_swap_rollovers(
        _utc(2026, 1, 13, 12), _utc(2026, 1, 15, 8), '17:00', NY, 2)
    assert [m for _, m in rollovers] == [1, 3]


def test_iter_skips_weekend():
    # Fri 12:00 → Mon 23:00 : Fri + Mon rollovers, Sat/Sun carry none
    rollovers = MarketCalendar.iter_swap_rollovers(
        _utc(2026, 1, 16, 12), _utc(2026, 1, 19, 23), '17:00', NY, 2)
    weekdays = [r.weekday() for r, _ in rollovers]
    assert 5 not in weekdays and 6 not in weekdays  # no Saturday / Sunday rollover
    assert weekdays == [4, 0]  # Friday, Monday


def test_iter_empty_window():
    t = _utc(2026, 1, 12, 10)
    assert MarketCalendar.iter_swap_rollovers(t, t, '17:00', NY, 2) == []


# --- next_swap_rollover ---

def test_next_rollover_normal():
    rollover, multiplier = MarketCalendar.next_swap_rollover(
        _utc(2026, 1, 12, 12), '17:00', NY, 2)
    assert rollover == _utc(2026, 1, 12, 22) and multiplier == 1


def test_next_rollover_triple():
    rollover, multiplier = MarketCalendar.next_swap_rollover(
        _utc(2026, 1, 14, 12), '17:00', NY, 2)
    assert rollover == _utc(2026, 1, 14, 22) and multiplier == 3


def test_next_rollover_skips_weekend():
    # Fri 23:00 (post-close) → next rollover is Monday
    rollover, _ = MarketCalendar.next_swap_rollover(
        _utc(2026, 1, 16, 23), '17:00', NY, 2)
    assert rollover.weekday() == 0


# --- next_market_close ---

def test_next_market_close_is_friday():
    close = MarketCalendar.next_market_close(_utc(2026, 1, 14, 12))  # Wed
    assert close.weekday() == 4 and close > _utc(2026, 1, 14, 12)


# --- next_market_open ---

def test_next_market_open_saturday_snaps_to_monday():
    # 2026-01-17 is a Saturday → next open is Monday 00:00 UTC
    open_ = MarketCalendar.next_market_open(_utc(2026, 1, 17, 14))
    assert open_ == _utc(2026, 1, 19, 0)  # Monday 00:00

def test_next_market_open_sunday_snaps_to_monday():
    # 2026-01-18 is a Sunday → next open is Monday 00:00 UTC
    open_ = MarketCalendar.next_market_open(_utc(2026, 1, 18, 9))
    assert open_ == _utc(2026, 1, 19, 0)

def test_next_market_open_already_open_unchanged():
    # 2026-01-14 is a Wednesday → already open, returned unchanged
    ref = _utc(2026, 1, 14, 12)
    assert MarketCalendar.next_market_open(ref) == ref

def test_next_market_open_skips_holiday():
    # 2025-12-25 (Christmas, Thursday) is a holiday → next open is Friday 2025-12-26 00:00
    open_ = MarketCalendar.next_market_open(_utc(2025, 12, 25, 10))
    assert open_ == _utc(2025, 12, 26, 0)
