"""
FiniexTestingIDE - Run Stream Types

What is read from a run stream without reading all of it (#611): its two ends.
"""

from dataclasses import dataclass
from typing import Any, Dict, Optional


@dataclass(frozen=True)
class RunStreamEnds:
    """
    A run stream's header, its first record and its latest one, read without the lines between.

    Args:
        header: The stream's first line when it is a header, else None
        first: The first record after the header, or None when the stream holds none yet
        last: The last complete record, or None when the stream holds none yet
        truncated_tail: Whether a last line cut off mid-write was left out
    """
    header: Optional[Dict[str, Any]]
    first: Optional[Dict[str, Any]]
    last: Optional[Dict[str, Any]]
    truncated_tail: bool
