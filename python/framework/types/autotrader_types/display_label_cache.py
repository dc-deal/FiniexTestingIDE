"""
FiniexTestingIDE - Display Label Cache
Immutable lookup tables for the live dashboard's ALGO STATE panel (#271).

Built once during AutoTrader startup (warmup phase) from the decision
logic and worker schemas. Passed read-only to the tick loop and the
display thread — frozen dataclass guarantees thread-safety.

Contents:
- Decision logic input params flagged display=True (for the Params: line)
- Worker output display=True keys + their display_labels
- Decision output display=True keys + their display_labels
"""

from dataclasses import dataclass, field
from typing import Dict, List, Tuple


@dataclass(frozen=True)
class DisplayLabelCache:
    """
    Immutable cache of schema display metadata for the live dashboard.

    Args:
        config_param_specs: List of (raw_key, display_key) tuples for
            decision logic input params with display=True. display_key
            is the display_label, or raw_key if no label is set.
        worker_display_output_keys: Per-worker instance map of the raw
            output keys flagged display=True. Used to filter worker
            outputs into the dashboard without re-reading schemas each tick.
        worker_output_labels: Per-worker instance map of
            {raw_output_key: display_label}. Only contains entries where
            display_label is set — callers fall back to raw_key.
        decision_output_labels: Decision logic output key → display_label.
            Only contains entries where display_label is set.
    """
    config_param_specs: Tuple[Tuple[str, str], ...] = field(default_factory=tuple)
    worker_display_output_keys: Dict[str, Tuple[str, ...]] = field(default_factory=dict)
    worker_output_labels: Dict[str, Dict[str, str]] = field(default_factory=dict)
    decision_output_labels: Dict[str, str] = field(default_factory=dict)
