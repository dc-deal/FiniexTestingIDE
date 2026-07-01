"""
FiniexTestingIDE - Signal JSONL Loader
Loads + validates + time-orders archived signal JSONL into a SignalSeries (#141).
"""

from datetime import datetime
from pathlib import Path
from typing import List, Optional

from python.framework.exceptions.signal_data_errors import SignalSchemaError
from python.framework.types.signal_data_types import SignalSeries, SignalSnapshot


# Major schema version this reader understands. A line with a different major
# version may carry a changed result structure → SignalSchemaError.
SUPPORTED_SCHEMA_MAJOR = '1'


def _schema_major(version: str) -> str:
    """Major component of a 'X.Y' schema version string."""
    return version.split('.', 1)[0]


def load_signal_series(
    path: Path,
    source: str,
    start: Optional[datetime] = None,
    end: Optional[datetime] = None,
) -> SignalSeries:
    """
    Load an archived signal JSONL into a validated, time-ordered SignalSeries.

    One physical line = one SignalSnapshot (envelope + collected_msc). The range
    keeps every snapshot with collected_msc <= end plus the last snapshot at or
    before start, so the first in-range tick still resolves a point-in-time value.

    Args:
        path: Archived JSONL file path
        source: Signal source label (e.g. 'llm_sentiment')
        start: Scenario start — keep one pre-start snapshot (None = no lower bound)
        end: Scenario end — drop later snapshots (None = no upper bound)

    Returns:
        SignalSeries with snapshots sorted ascending by collected_msc

    Raises:
        SignalSchemaError: If a line declares an incompatible schema major version
    """
    snapshots: List[SignalSnapshot] = []
    with open(path, 'r', encoding='utf-8') as handle:
        for line in handle:
            line = line.strip()
            if not line:
                continue
            snapshot = SignalSnapshot.model_validate_json(line)
            if _schema_major(snapshot.schema_version) != SUPPORTED_SCHEMA_MAJOR:
                raise SignalSchemaError(
                    f"Signal line schema_version '{snapshot.schema_version}' is not "
                    f"compatible (reader supports major {SUPPORTED_SCHEMA_MAJOR}.x)."
                )
            snapshots.append(snapshot)

    snapshots.sort(key=lambda s: s.collected_msc)

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
