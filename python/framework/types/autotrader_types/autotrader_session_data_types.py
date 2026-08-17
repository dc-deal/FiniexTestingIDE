"""
FiniexTestingIDE - AutoTrader Session Data Types

The prepared data a mock session starts from (#438): the replayed data package plus
the signal metadata the end-of-session report needs. Both come out of the SAME shared
MountPreparer run, so the live report renders the 📡 section from the same source the
sim batch does (#433) — no second build path, no re-read of the parquet at session end.
"""

from dataclasses import dataclass, field
from typing import Dict, Tuple

from python.framework.types.process_data_types import ProcessDataPackage
from python.framework.types.scenario_types.scenario_set_types import SignalScenarioInfo


@dataclass
class PreparedSessionData:
    """Result of the mock session's data preparation."""
    package: ProcessDataPackage
    # (signal source, symbol) → coverage + the scenario window bound to it
    signal_scenario_map: Dict[Tuple[str, str], SignalScenarioInfo] = field(
        default_factory=dict)
