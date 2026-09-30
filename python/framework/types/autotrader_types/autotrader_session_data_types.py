"""
FiniexTestingIDE - AutoTrader Session Data Types

The prepared data a mock session starts from (#438): the replayed data package plus
the signal metadata the end-of-session report needs. Both come out of the SAME shared
MountPreparer run, so the live report renders the 📡 section from the same source the
sim batch does (#433) — no second build path, no re-read of the parquet at session end.
"""

from dataclasses import dataclass, field
from typing import Dict, Optional, Tuple

from python.framework.discoveries.signal_coverage.signal_scenario_info import SignalScenarioInfo
from python.framework.types.process_data_types import ProcessDataPackage
from python.framework.types.scenario_types.scenario_set_types import SingleScenario


@dataclass
class PreparedSessionData:
    """Result of the mock session's data preparation."""
    package: ProcessDataPackage
    # (signal source, symbol) → coverage + the scenario window bound to it
    signal_scenario_map: Dict[Tuple[str, str], SignalScenarioInfo] = field(
        default_factory=dict)
    # The one scenario the profile describes, AFTER the mount filled what it read — data format
    # versions, origins, price bases. The session's ledger row takes its consumption record from
    # it, exactly as a backtest's does, so a mock session is no longer recorded as a stream.
    scenario: Optional[SingleScenario] = None
