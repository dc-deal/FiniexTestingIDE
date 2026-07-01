"""
FiniexTestingIDE - Signal Parquet Reader

Reads the imported signal parquet (#429) into a runtime SignalSeries, projected to ONE
symbol's consumed fields (the #128/#429 field projection). Standalone so both the sim
index-resolution path (SharedDataPreparator) and a future AutoTrader mock sentiment_source
share it — mirroring the shared tick parquet reader.
"""

from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from python.framework.types.signal_data_types import (
    SIGNAL_ENVELOPE_SYMBOL, SIGNAL_RUNTIME_COLUMNS, SentimentResult,
    SignalParquetColumn, SignalSeries, SignalSnapshot)


def load_signal_series_from_parquet(
    paths: List[Path],
    source: str,
    symbol: str,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> SignalSeries:
    """
    Read the signal parquet(s) into a SignalSeries projected to one symbol (#429).

    One SignalSnapshot per envelope (collected_msc): result = [the symbol's SentimentResult]
    when present, else [] — an envelope where the symbol was absent (partial/error) resolves
    to a defensive HOLD, matching the v0 provider. Range trim mirrors the JSONL loader: keep
    every snapshot with collected_msc <= end plus the last one at/before start.

    Args:
        paths: Signal parquet files (resolved from the index)
        source: Signal source label stamped on the series
        symbol: Symbol to project
        start: Scenario start — keep one pre-start snapshot (None = no lower bound)
        end: Scenario end — drop later snapshots (None = no upper bound)

    Returns:
        SignalSeries with per-envelope snapshots for the symbol, ascending by collected_msc
    """
    cols = sorted(SIGNAL_RUNTIME_COLUMNS)
    frames = [pd.read_parquet(path, columns=cols) for path in paths]
    if not frames:
        return SignalSeries(source=source, snapshots=[])
    df = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]

    keep = df[df[SignalParquetColumn.SYMBOL.value].isin(
        [symbol, SIGNAL_ENVELOPE_SYMBOL])]

    # One row per collected_msc; the symbol-specific row wins over the sentinel (and
    # last-wins on a duplicate msc mirrors the provider's bisect_right resolution).
    by_msc: Dict[int, Tuple[str, tuple]] = {}
    for row in keep.itertuples(index=False):
        msc = int(getattr(row, SignalParquetColumn.COLLECTED_MSC.value))
        row_symbol = getattr(row, SignalParquetColumn.SYMBOL.value)
        existing = by_msc.get(msc)
        if existing is not None and existing[0] == symbol and row_symbol != symbol:
            continue
        by_msc[msc] = (row_symbol, row)

    snapshots: List[SignalSnapshot] = []
    for msc in sorted(by_msc.keys()):
        row_symbol, row = by_msc[msc]
        collected = datetime.fromtimestamp(msc / 1000.0, tz=timezone.utc)
        result: List[SentimentResult] = []
        if row_symbol == symbol:
            result = [SentimentResult(
                symbol=symbol,
                signal=getattr(row, SignalParquetColumn.SIGNAL.value),
                sentiment_score=getattr(row, SignalParquetColumn.SENTIMENT_SCORE.value),
                confidence=getattr(row, SignalParquetColumn.CONFIDENCE.value),
                reasoning=getattr(row, SignalParquetColumn.REASONING.value),
                urgency=getattr(row, SignalParquetColumn.URGENCY.value),
                is_breaking=bool(getattr(row, SignalParquetColumn.IS_BREAKING.value)),
            )]
        snapshots.append(SignalSnapshot(
            schema_version=getattr(row, SignalParquetColumn.SCHEMA_VERSION.value),
            status=getattr(row, SignalParquetColumn.STATUS.value),
            collected_msc=collected,
            result=result,
        ))

    if end is not None:
        snapshots = [s for s in snapshots if s.collected_msc <= end]
    if start is not None:
        keep_from = 0
        for idx, snapshot in enumerate(snapshots):
            if snapshot.collected_msc <= start:
                keep_from = idx
            else:
                break
        snapshots = snapshots[keep_from:]

    return SignalSeries(source=source, snapshots=snapshots)
