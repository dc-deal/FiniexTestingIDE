"""
FiniexTestingIDE - Recorded Scenario Types (#512)
What a backtest of one session segment's window is built from: every input as the session recorded
it, never as the configuration files and caches stand on the day of the comparison.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict

from python.framework.types.run_purpose_types import RunPurpose


@dataclass(frozen=True)
class RecordedScenarioInputs:
    """
    The recorded inputs of one segment's backtest.

    Args:
        scenario_set_name: The generated set's name — also its file name, so unique per comparison
        scenario_name: The scenario's name in the set
        rendered_profile: The session's rendered profile, the document its `rendered_config_id`
            names
        warmup_from_run: The run whose recorded warmup history the backtest starts from — the
            segment's first session
        broker_config_document: The broker configuration the session froze at its start (its
            `broker_config_id`)
        start: The window's start — the segment's first tick
        end: The window's end — the close of its last compared trading day
        balances: The account at the segment's start, every currency held
        run_purpose: What the backtest's run is for — REGULAR for a comparison, FIXTURE in a test
    """

    scenario_set_name: str
    scenario_name: str
    rendered_profile: Dict[str, Any]
    warmup_from_run: str
    broker_config_document: str
    start: datetime
    end: datetime
    balances: Dict[str, float]
    run_purpose: RunPurpose = RunPurpose.REGULAR
