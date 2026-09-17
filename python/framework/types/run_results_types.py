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
    objective: str = 'expectancy'   # the spec's ranking objective (recorded so report defaults to it)
    maximize: bool = True           # the spec's ranking direction


@dataclass
class RunProvenance:
    """Per-run provenance written to the ledger next to the run's KPIs."""
    param_hash: str                 # fingerprint of the effective strategy_config (leading key)
    status: str                     # 'ok' | 'error' (from the canonical WarningsErrorsOutcome)
    error: Optional[str]            # failure reason when status == 'error', else None
    run_id: str                     # run-timestamp dir name (join key → full run io/)
    run_timestamp: datetime         # UTC
    scenario_set_name: str
    # The program version that produced the run. Git identity says WHICH CODE; this says
    # which RELEASE it was, and a run artifact could not answer that at all before — the
    # field existed in app_config and was read by the HTTP API alone.
    app_version: str
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
    sweep_objective: Optional[str] = None    # the sweep spec's objective (report defaults to it)
    sweep_maximize: Optional[bool] = None    # the sweep spec's rank direction
    # ---- What this run CONSUMED (#518) -------------------------------------------------
    # Recorded HERE and deliberately not in the run header. The header is written at the run's
    # START, before anything is mounted, and has a write and a read and no update path by
    # design — at that moment nothing is known about what will be read. This record is built
    # from a FINISHED run, so it is the first artifact that can answer the question at all.
    #
    # The argument is `logic_version`'s, one drawer over: a ranking that cannot tell which DATA
    # a row was produced over is comparing runs that read different things under one column
    # name. For the thirty-day parity proof that is not a nicety — a live run and the backtest
    # it is compared against have to be shown to have read the same archive.
    #
    # They sit apart from `data_broker_type`, which they belong beside, only because a dataclass
    # puts every defaulted field after every undefaulted one.
    #
    # `input_plane` is what keeps an empty value honest: a LIVE session consumes a socket and
    # has no archive input, so its three joined strings are empty BY CONSTRUCTION. Without this
    # field that emptiness would be indistinguishable from a sim run whose recording broke —
    # the same bytes for "nothing to read" and "we were not looking".
    input_plane: str = ''                   # 'archive' (sim) | 'stream' (live)
    data_format_versions: str = ''          # distinct, sorted, comma-joined
    origin_classes: str = ''                # distinct, sorted, comma-joined
    origin_evidence_grades: str = ''        # distinct, sorted, comma-joined
    input_files: int = 0                    # files the scenarios actually read
    unstamped_input_files: int = 0          # of those, the ones not production-and-stamped
