"""
Untrusted Arrival Times in the Tick Preparation Tests (#460).

A backtest orders its ticks by when they arrived, and its processing budget is timed on the same
stamp. A file whose arrival time says nothing about arrival — none recorded, one reconstructed,
one a drifting clock set off — has it cleared on the way into the scenario, so its ticks order and
are budgeted on their event time. These tests drive the preparation over a small archive of one
flagged and one clean file, once for every deficiency, and hold it to clearing exactly where the
deficiency leaves the arrival untrusted.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List
from unittest.mock import MagicMock

import pandas as pd
import pytest

from python.data_management.index.tick_index_manager import TickIndexManager
from python.framework.data_preparation.shared_data_preparator import SharedDataPreparator
from python.framework.types.data_deficiency_types import DataDeficiency
from python.framework.types.process_data_types import TickRequirement

_DAY = datetime(2021, 3, 1, tzinfo=timezone.utc)
_STAMP = '%Y-%m-%dT%H:%M:%S'
_TICKS = 6
_FLAGGED_LAG_MS = 7_800
_CLEAN_LAG_MS = 14


def _write_file(folder: Path, name: str, start: datetime, lag_ms: int,
                deficiencies: List[DataDeficiency]) -> Dict[str, object]:
    """
    One quoted tick file, a tick every ten minutes arriving lag_ms after it happened, and its
    index entry.

    Args:
        folder: Where the file goes
        name: File name
        start: Its first tick
        lag_ms: How long after the event time each tick arrived
        deficiencies: What the index says the file lacks

    Returns:
        The index entry
    """
    times = [start + timedelta(minutes=10 * i) for i in range(_TICKS)]
    event = [int(t.timestamp() * 1000) for t in times]
    frame = pd.DataFrame({
        'timestamp': times,
        'time_msc': event,
        'collected_msc': [msc + lag_ms for msc in event],
        'bid': [99.9] * _TICKS,
        'ask': [100.1] * _TICKS,
        'last': [100.0] * _TICKS,
        'volume': [0.01] * _TICKS,
    })
    path = folder / name
    frame.to_parquet(path)
    return {'path': str(path), 'start_time': times[0].strftime(_STAMP),
            'end_time': times[-1].strftime(_STAMP), 'tick_count': _TICKS,
            'deficiencies': deficiencies}


def _preparator(entries: List[Dict[str, object]]) -> SharedDataPreparator:
    """
    The preparator over the given archive, its index built by hand.

    Args:
        entries: The index entries

    Returns:
        The preparator
    """
    index = TickIndexManager.__new__(TickIndexManager)
    index.index = {'kraken_spot': {'ETHUSD': entries}}
    index.logger = MagicMock()
    preparator = SharedDataPreparator.__new__(SharedDataPreparator)
    preparator._logger = MagicMock()
    preparator.tick_index_manager = index
    return preparator


@pytest.mark.parametrize('deficiency', list(DataDeficiency), ids=lambda d: d.value)
def test_the_arrival_is_cleared_exactly_where_the_deficiency_leaves_it_untrusted(
        tmp_path, deficiency):
    entries = [_write_file(tmp_path, 'flagged.parquet', _DAY, _FLAGGED_LAG_MS, [deficiency]),
               _write_file(tmp_path, 'clean.parquet', _DAY + timedelta(days=1), _CLEAN_LAG_MS,
                           [])]
    ticks, _, _ = _preparator(entries).prepare_ticks([TickRequirement(
        scenario_name='scenario', broker_type='kraken_spot', symbol='ETHUSD',
        start_time=_DAY, end_time=_DAY + timedelta(days=2))])

    lags = [tick['collected_msc'] - tick['time_msc'] if tick['collected_msc'] else None
            for tick in ticks['scenario']]
    flagged = ([None] * _TICKS if deficiency.leaves_arrival_untrusted()
               else [_FLAGGED_LAG_MS] * _TICKS)
    assert lags == flagged + [_CLEAN_LAG_MS] * _TICKS
