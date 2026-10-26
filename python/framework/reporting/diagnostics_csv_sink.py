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

An AutoTrader session also streams every row as it is appended (#476), into a JSONL file beside
the table: the table is written at the end, and a session killed before it would keep no row.
"""

from pathlib import Path
from typing import Any, Dict, List, Optional

from python.framework.exceptions.data_home_errors import DirectoryCreationDeniedError
from python.framework.reporting.io.run_stream_appender import RunStreamAppender
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
        # Where each row also goes as it is appended — a session's stream; None writes nothing
        self._stream: Optional[RunStreamAppender] = None

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
        if self._stream is not None:
            self._stream.write(self._declared(row))

    def get_columns(self) -> List[str]:
        """
        The declared columns, in order.

        Returns:
            The column names
        """
        return list(self._columns)

    def attach_stream(self, stream: RunStreamAppender) -> None:
        """
        Write every row into a stream as well — the rows so far at once, each later one as it is
        appended (#476).

        Args:
            stream: The opened stream
        """
        self._stream = stream
        for row in self._rows:
            stream.write(self._declared(row))

    def _declared(self, row: Dict[str, Any]) -> Dict[str, Any]:
        """
        A row as a stream line holds it: the declared columns, in order, a missing one empty.

        Args:
            row: The appended row

        Returns:
            Column → value for the declared columns, every value one a JSON line can hold
        """
        return {column: _line_value(row.get(column)) for column in self._columns}

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
        except DirectoryCreationDeniedError:
            # A directory the IDE may not create is a refusal that stops the run, not a table
            # that failed to write
            raise
        except Exception as e:
            print(f'Warning: Failed to write diagnostics CSV {self._name}: {e}')
            return None


def _line_value(value: Any) -> Any:
    """
    One cell as a stream line holds it: JSON's own types as they are, anything else as the table
    writes it.

    The table turns every cell into text, so a strategy may append a datetime, a numpy number or an
    enum and the backtest's table takes it. The stream keeps numbers and flags typed and writes
    everything else as the table would — a row the table takes must never end a session through
    its stream (#476).

    Args:
        value: The appended cell

    Returns:
        None, a bool, an int, a float, a str — or the cell's text
    """
    if value is None or isinstance(value, (bool, int, float, str)):
        return value
    # A numpy scalar holds a Python number or flag; an array of more than one value has none
    item = getattr(value, 'item', None)
    if callable(item):
        try:
            held = item()
        except (TypeError, ValueError):
            held = None
        if isinstance(held, (bool, int, float, str)):
            return held
    return str(value)


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
