"""
The warmup a backtest of a session's window started from (#512) — the injected bars held to the
session's record bar by bar before measure 1, and each session's record held to the archive's bars.

Records are built in place; each test changes one thing — a bar, a timeframe, the instant the bars
are complete at, a record missing — and the check names it.
"""

from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd
import pytest

from python.framework.bars.warmup_record_reader import read_warmup_record_at
from python.framework.reporting.builders.divergence.warmup_check_builder import (
    check_warmup_injection,
    compare_warmup_with_archive,
)
from python.framework.reporting.divergence_report_coordinator import warmup_injection_of
from python.framework.types.api.divergence_report_types import WarmupInjectionState
from python.framework.types.market_types.warmup_history_types import (
    WarmupHistoryRecord,
    WarmupSource,
    WarmupTimeframeRecord,
)

_SELECTED_FOR = '2026-11-24T00:00:00+00:00'

_Bar = Tuple[str, float, float, float, float, float, int]


def _bars() -> List[_Bar]:
    return [('2026-11-22T00:00:00+00:00', 3000.0, 3050.0, 2980.0, 3020.0, 812.5, 4100),
            ('2026-11-23T00:00:00+00:00', 3020.0, 3075.0, 3001.0, 3060.0, 905.25, 4380)]


def _record(bars: Optional[Dict[str, List[_Bar]]] = None,
            selected_for: str = _SELECTED_FOR) -> WarmupHistoryRecord:
    return WarmupHistoryRecord(
        symbol='ETHUSD', source=WarmupSource.VENUE, read_at=selected_for,
        selected_for=selected_for, first_tick=None, reread_at_first_tick=[],
        timeframes={timeframe: WarmupTimeframeRecord(required=len(rows), bars=rows)
                    for timeframe, rows in (bars if bars is not None else {'D1': _bars()}).items()})


class TestTheInjectedWarmup:

    def test_the_bars_as_recorded_are_equal(self):
        check = check_warmup_injection(_record(), _record())
        assert check.state is WarmupInjectionState.EQUAL and not check.differences
        assert 'D1 2 bars' in check.detail

    def test_a_changed_bar_is_named_with_both_values(self):
        injected = _bars()
        injected[1] = injected[1][:4] + (3061.0,) + injected[1][5:]
        check = check_warmup_injection(_record(), _record({'D1': injected}))
        assert check.state is WarmupInjectionState.DIFFERENT
        difference, = check.differences
        assert 'D1 bar 2026-11-23T00:00:00+00:00' in difference and '3061.0' in difference

    def test_a_lost_bar_and_a_lost_timeframe_are_named(self):
        recorded = _record({'D1': _bars(), 'H4': _bars()})
        check = check_warmup_injection(recorded, _record({'D1': _bars()[:1]}))
        assert check.state is WarmupInjectionState.DIFFERENT
        assert 'D1: 2 bars recorded, 1 injected' in check.differences
        assert 'H4: missing from the injection' in check.differences

    def test_bars_complete_at_another_instant_differ(self):
        check = check_warmup_injection(
            _record(), _record(selected_for='2026-11-24T00:00:05+00:00'))
        assert check.state is WarmupInjectionState.DIFFERENT
        assert 'complete at' in check.differences[0]

    def test_no_record_on_either_side_is_not_checked_never_equal(self):
        check = check_warmup_injection(None, None)
        assert check.state is WarmupInjectionState.NOT_CHECKED
        assert 'neither' in check.detail

    def test_a_record_on_one_side_only_is_not_checked_never_equal(self):
        check = check_warmup_injection(_record(), None)
        assert check.state is WarmupInjectionState.NOT_CHECKED
        assert 'the backtest recorded no warmup' in check.detail

    def test_an_unreadable_record_is_not_checked(self, tmp_path: Path):
        broken = tmp_path / 'warmup_history.json'
        broken.write_text('{"symbol": "ETHUSD"}', encoding='utf-8')
        record, why = read_warmup_record_at(broken)
        assert record is None and 'cannot be read' in why
        check = warmup_injection_of(broken, None)
        assert check.state is WarmupInjectionState.NOT_CHECKED and 'cannot be read' in check.detail

    def test_a_missing_file_is_no_record_not_a_fault(self, tmp_path: Path):
        assert read_warmup_record_at(tmp_path / 'none.json') == (None, '')
        assert read_warmup_record_at(None) == (None, '')


def _archive(close: float = 3060.0, drop_last: bool = False,
             volume_share: float = 1.0) -> pd.DataFrame:
    rows = [{'timestamp': pd.Timestamp(bar[0]), 'open': bar[1], 'high': bar[2], 'low': bar[3],
             'close': bar[4], 'volume': bar[5] * volume_share, 'tick_count': bar[6]}
            for bar in _bars()]
    rows[1]['close'] = close
    rows.insert(0, {'timestamp': pd.Timestamp('2026-11-21T00:00:00+00:00'), 'open': 1.0,
                    'high': 1.0, 'low': 1.0, 'close': 1.0, 'volume': 1.0, 'tick_count': 1})
    return pd.DataFrame(rows[:-1] if drop_last else rows)


class TestTheArchiveLine:

    def test_the_archive_holding_the_same_bars_shows_no_difference(self):
        line, = compare_warmup_with_archive('s1', _record(), {'D1': _archive()})
        assert (line.compared, line.differing, line.missing_in_archive) == (2, 0, 0)
        assert line.recorded_to_archived_volume == 1.0

    def test_a_volume_the_collector_saw_less_of_is_a_ratio_never_a_difference(self):
        line, = compare_warmup_with_archive('s1', _record(), {'D1': _archive(volume_share=0.8)})
        assert line.differing == 0 and not line.examples
        assert line.recorded_to_archived_volume == pytest.approx(1.25)

    def test_a_differing_bar_is_counted_and_named(self):
        line, = compare_warmup_with_archive('s1', _record(), {'D1': _archive(close=3058.5)})
        assert (line.compared, line.differing) == (2, 1)
        assert line.examples == [
            '2026-11-23T00:00:00+00:00: close 3060 recorded, 3058.5 archived']

    def test_a_period_the_archive_lacks_is_counted_apart(self):
        line, = compare_warmup_with_archive('s1', _record(), {'D1': _archive(drop_last=True)})
        assert (line.compared, line.missing_in_archive) == (1, 1)

    def test_a_timeframe_without_a_bar_file_is_not_compared(self):
        line, = compare_warmup_with_archive('s1', _record(), {'D1': None})
        assert line.compared == 0 and 'no bar file' in line.not_compared_because
        assert line.recorded_to_archived_volume is None
