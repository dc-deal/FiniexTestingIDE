"""
Run-results ledger types.

Runtime domain types for the persistent run-results ledger (the substrate of the
Parameter Optimization system). `RunProvenance` is the per-run provenance bundle
written alongside the run's KPIs; `SweepContext` is the optional sweep tagging a
combination carries into a batch so the ledger row can be grouped by sweep.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional


@dataclass
class SweepContext:
    """Sweep tagging threaded into a single combination's batch run."""
    sweep_id: str
    sweep_params: Dict[str, Any]    # the combination's concrete grid point {path: value}


@dataclass
class RunProvenance:
    """Per-run provenance written to the ledger next to the run's KPIs."""
    param_hash: str                 # fingerprint of the effective strategy_config (leading key)
    status: str                     # 'ok' | 'error' (from the canonical WarningsErrorsOutcome)
    error: Optional[str]            # failure reason when status == 'error', else None
    run_id: str                     # run-timestamp dir name (join key → full run io/)
    run_timestamp: datetime         # UTC
    scenario_set_name: str
    git_commit: Optional[str]
    git_branch: Optional[str]
    git_dirty: bool
    decision_logic_type: str
    decision_version: str
    worker_versions: Dict[str, str]     # worker instance name → ComponentMetadata.version
    config_snapshot: str                # full resolved strategy_config (JSON string)
    symbols: List[str]
    data_broker_type: str
    sweep_id: Optional[str] = None      # null for non-sweep runs
    sweep_params: Optional[Dict[str, Any]] = None
