"""
FiniexTestingIDE - Run Header IO
Write and read the one artifact that says what a run is, and build the parts of it both
pipelines stamp the same way.
"""

from pathlib import Path
from typing import List

from python.framework.types.api.report_types import DataWindow, RunHeader
from python.framework.types.scenario_types.scenario_set_types import SingleScenario

RUN_HEADER_ARTIFACT = 'header.json'


def write_run_header(header: RunHeader, run_dir: Path) -> Path:
    """
    Write a run's header into its own directory, at the top — not under io/.

    Deliberately beside the logs rather than with the report artifacts: io/ holds what a run
    PRODUCED, and it only exists once reporting has run. The header says what the run IS and
    has to be there from the first second, including for a run that never reaches reporting.

    Args:
        header: The run's identity
        run_dir: The run's own directory

    Returns:
        The written path
    """
    run_dir.mkdir(parents=True, exist_ok=True)
    path = run_dir / RUN_HEADER_ARTIFACT
    path.write_text(header.model_dump_json(indent=2), encoding='utf-8')
    return path


def read_run_header(path: Path) -> RunHeader:
    """
    Read a run header.

    Args:
        path: The header file

    Returns:
        The parsed header
    """
    return RunHeader.model_validate_json(path.read_text(encoding='utf-8'))


def data_windows_of(scenarios: List[SingleScenario]) -> List[DataWindow]:
    """
    The market window each scenario was declared to cover — one per scenario, never one span.

    ONE derivation for both pipelines: a backtest's scenarios and the single scenario a mock
    session replays carry their window the same way, so both headers answer the date filter in
    the same shape.

    Args:
        scenarios: The run's scenarios as configured (a tick-limited one has no end date)

    Returns:
        One window per scenario, in the run's scenario order
    """
    return [DataWindow(
        unit_name=scenario.name,
        start_date=scenario.start_date.isoformat(),
        end_date=scenario.end_date.isoformat() if scenario.end_date else None)
        for scenario in scenarios]
