"""
FiniexTestingIDE - Warmup Record Reader (#512)

Reads back the warmup history a run recorded (`io/warmup_history.json`), by the run's id. It lives
apart from WarmupHistory on purpose: the loader of a backtest of a session's window reads the record
before any process starts and hands the scenario its content, so the scenario process — the path the
comparison's gate holds to the session's release — never touches the run index.
"""

import json
from typing import Optional

from python.framework.store.entry_members.run_entry_reader import RunEntryReader
from python.framework.types.market_types.warmup_history_types import WarmupHistoryRecord
from python.framework.types.store_member_types import RunEntryMemberId


def read_warmup_record(run_id: str, reader: Optional[RunEntryReader] = None) -> WarmupHistoryRecord:
    """
    The warmup history a run recorded, read back by the run's id.

    Args:
        run_id: The run that recorded it
        reader: The run reader; the configured one when omitted

    Returns:
        The record
    """
    path = (reader or RunEntryReader()).path_of(run_id, RunEntryMemberId.WARMUP_HISTORY)
    if path is None or not path.exists():
        raise FileNotFoundError(
            f'Run {run_id} has no recorded warmup history '
            f'({path if path is not None else "the run index does not carry the run"}) — a '
            f'session records one only when a worker requires bars.')
    with open(path, 'r', encoding='utf-8') as handle:
        return WarmupHistoryRecord.from_dict(json.load(handle))
