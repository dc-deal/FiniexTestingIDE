"""
Signal-source-to-scenario mapping, held beside the manager that builds it.

The signal sibling of `BrokerScenarioInfo`, and it moved out of the type module for the same
reason: `coverage` is a built report object rather than a config section, so its fields are
runtime COLLABORATORS and §6's exception applies.

`SignalScenarioUsage` stays in `scenario_set_types` — its fields are plain data, and that is
the test §6 states.
"""

from dataclasses import dataclass, field
from typing import List

from python.framework.discoveries.signal_coverage.signal_coverage_report import (
    SignalCoverageReport,
)
from python.framework.types.scenario_types.scenario_set_types import SignalScenarioUsage


@dataclass
class SignalScenarioInfo:
    """
    Internal mapping of a signal source/symbol to the scenarios using it (#433).

    The signal sibling of BrokerScenarioInfo: it carries the archive-side coverage
    (built once in the preparation Phase 1) plus every scenario window bound to it,
    so the report renders both planes without re-reading the parquet.
    """
    data_sentiment_type: str
    symbol: str
    coverage: SignalCoverageReport
    usages: List[SignalScenarioUsage] = field(default_factory=list)
