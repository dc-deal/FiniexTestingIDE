"""
FiniexTestingIDE - Diagnostics CSV Sink (#376)

Generic per-run CSV channel for strategy-owned diagnostics (signal funnels,
near-miss analysis, per-attempt quality metrics). The framework owns the file
logistics — run directory, naming, lifecycle, both pipelines — while the
strategy owns the schema: it declares columns and appends rows.

Distinct from the trade-domain event_stream_csv_writer (fixed schema, post-loop
reconstruction). Rows here accumulate in memory during the run (decision moments
are low-frequency, not per-tick) and are flushed once at run end, so nothing
touches the hot tick path. A no-op where the run has no writer.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

from python.framework.store.entry_members.run_entry_writer import RunEntryWriter
from python.framework.types.store_member_types import RunEntryMemberId


class DiagnosticsCsvSink:
    """A named, algo-declared diagnostics CSV — columns declared, rows appended."""

    def __init__(self, name: str, columns: List[str]):
        """
        Initialize a diagnostics sink.

        Args:
            name: Filename stem (e.g. 'setup_funnel' → setup_funnel.csv)
            columns: Ordered column names — the CSV header
        """
        self._name = name
        self._columns = list(columns)
        self._rows: List[Dict[str, Any]] = []

    def get_name(self) -> str:
        """
        The sink name (filename stem).

        Returns:
            The name passed at construction
        """
        return self._name

    def append_row(self, row: Dict[str, Any]) -> None:
        """
        Append one diagnostic row (buffered in memory).

        Keys not in the declared columns are ignored on flush; declared columns
        missing from the row render as empty cells.

        Args:
            row: Column-name → value mapping for this row
        """
        self._rows.append(row)

    def get_row_count(self) -> int:
        """
        How many rows the strategy has appended.

        Returns:
            The buffered row count
        """
        return len(self._rows)

    def flush(
        self,
        writer: Optional[RunEntryWriter],
        scenario_suffix: Optional[str] = None,
    ) -> Optional[Path]:
        """
        Write the buffered rows to a CSV in the run's diagnostics folder.

        No-op when there is no writer (a test without a run) or no rows were
        appended — mirrors the EventStreamWriter convention.

        Args:
            writer: The run's writer
            scenario_suffix: Optional scenario name → <name>_<suffix>.csv
                (sim writes all scenarios into one run dir; the suffix
                disambiguates and aligns with events_<scenario>.csv)

        Returns:
            Path to the written CSV, or None if nothing was written
        """
        if writer is None or not self._rows:
            return None

        if scenario_suffix:
            member_id, names = RunEntryMemberId.SCENARIO_DIAGNOSTICS, {
                'sink': self._name, 'scenario': scenario_suffix}
        else:
            member_id, names = RunEntryMemberId.SESSION_DIAGNOSTICS, {'sink': self._name}
        try:
            return writer.write_table(member_id, self._columns, self._rows,
                                      ignore_extra_columns=True, **names)
        except Exception as e:
            print(f'Warning: Failed to write diagnostics CSV {self._name}: {e}')
            return None


def flush_decision_diagnostics(
    decision_logic,
    writer: Optional[RunEntryWriter],
    scenario_suffix: Optional[str] = None,
) -> None:
    """
    Flush all of a decision logic's diagnostics sinks at run end.

    Strategy-owned diagnostics land in a dedicated `diagnostics/` subfolder of the
    run directory — separate from the framework's trade-event `events.csv` — in
    both pipelines, keeping the run dir tidy when many scenarios produce many CSVs.
    Shared by both pipelines (sim subprocess + AutoTrader session). Safe when the
    logic declared no sinks or file logging is disabled.

    Args:
        decision_logic: The AbstractDecisionLogic instance for the run
        writer: The run's writer, or None when there is no run directory
        scenario_suffix: Optional scenario name for the sim per-scenario filename
    """
    for sink in decision_logic.get_diagnostics_sinks():
        sink.flush(writer, scenario_suffix)
