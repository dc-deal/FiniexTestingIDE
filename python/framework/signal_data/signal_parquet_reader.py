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
import pyarrow.parquet as pq

from python.framework.types.signal_data_types import (
    SIGNAL_ENVELOPE_SYMBOL,
    SIGNAL_RUNTIME_COLUMNS,
    SentimentResult,
    SignalParquetColumn,
    SignalSeries,
    SignalSnapshot,
)


def _optional_int(row, column: str) -> Optional[int]:
    """
    Read a nullable integer column off a row.

    Args:
        row: A DataFrame row (itertuples)
        column: Column name

    Returns:
        The value as int, or None when absent / null (the pre-stream era)
    """
    value = getattr(row, column, None)
    if value is None or value == '' or pd.isna(value):
        return None
    return int(value)


def load_signal_series_from_parquet(
    paths: List[Path],
    signal_kind: str,
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
        signal_kind: Payload kind label stamped on the series
        symbol: Symbol to project
        start: Scenario start — keep one pre-start snapshot (None = no lower bound)
        end: Scenario end — drop later snapshots (None = no upper bound)

    Returns:
        SignalSeries with per-envelope snapshots for the symbol, ascending by collected_msc
    """
    # Project only what a file actually has: the stream-identity columns (#141 Part 2a)
    # are absent from every parquet written before the stream contract, and an archive
    # spanning the boundary holds both shapes. Missing columns are filled with None after
    # the concat so the construction below stays uniform.
    frames = []
    for path in paths:
        available = set(pq.read_schema(path).names)
        frames.append(pd.read_parquet(
            path, columns=sorted(SIGNAL_RUNTIME_COLUMNS & available)))
    if not frames:
        return SignalSeries(signal_kind=signal_kind, snapshots=[])
    df = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]
    for column in SIGNAL_RUNTIME_COLUMNS - set(df.columns):
        df[column] = None

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
                basis=getattr(row, SignalParquetColumn.BASIS.value),
                evidence_as_of=_optional_int(
                    row, SignalParquetColumn.EVIDENCE_AS_OF.value),
            )]
        snapshots.append(SignalSnapshot(
            schema_version=getattr(row, SignalParquetColumn.SCHEMA_VERSION.value),
            status=getattr(row, SignalParquetColumn.STATUS.value),
            collected_msc=collected,
            seq=_optional_int(row, SignalParquetColumn.SEQ.value),
            stream_epoch=_optional_int(row, SignalParquetColumn.STREAM_EPOCH.value),
            available_msc=_optional_int(
                row, SignalParquetColumn.AVAILABLE_MSC.value),
            envelope_evidence_as_of=_optional_int(
                row, SignalParquetColumn.ENVELOPE_EVIDENCE_AS_OF.value),
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

    return SignalSeries(signal_kind=signal_kind, snapshots=snapshots)
