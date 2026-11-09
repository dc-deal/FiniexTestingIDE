"""
The sweep list's fold table (#557) — one ledger row of a sweep's combination per row.

A sweep writes one ledger row per combination, unit and booking period, so its counts are counts of
distinct runs, never of rows. Its description is read from the first row the ledger returns, as the
list always read it.
"""

from datetime import datetime
from typing import Any, Dict, Optional, Sequence

from python.framework.types.aggregation_types import (
    FigureDeclaration,
    FoldTable,
    GroupOrder,
    Reduction,
)
from python.framework.types.api.report_types import RunResultRow, SweepSummary


def run_start(row: Any) -> Optional[datetime]:
    """
    A combination's start, parsed from the row's run timestamp.

    Args:
        row: A ledger row

    Returns:
        The start, or None when the row carries no timestamp
    """
    return datetime.fromisoformat(row.run_timestamp) if row.run_timestamp else None


def errored_run(row: Any) -> Optional[str]:
    """
    The run a row belongs to, when that run ended in an error.

    Args:
        row: A ledger row

    Returns:
        The run id, or None for a run that did not error
    """
    return row.run_id if row.status == 'error' else None


def base_config(row: Any) -> str:
    """
    The scenario set the sweep expanded — its name before the combination suffix.

    Args:
        row: A ledger row

    Returns:
        The base scenario set name
    """
    return row.scenario_set_name.split('__', 1)[0]


def sweep_duration(combined: Dict[str, Any], rows: Sequence[Any]) -> float:
    """
    From the first combination's start to the last one's.

    Args:
        combined: The figures being built
        rows: The sweep's rows

    Returns:
        The seconds between them; 0.0 with fewer than two starts
    """
    stamps = sorted(start for start in (run_start(row) for row in rows) if start is not None)
    return (stamps[-1] - stamps[0]).total_seconds() if len(stamps) > 1 else 0.0


def runs_without_error(combined: Dict[str, Any], rows: Sequence[Any]) -> int:
    """
    The combinations that did not error.

    Args:
        combined: The figures being built, the two counts already folded
        rows: The sweep's rows

    Returns:
        The distinct runs less the distinct errored ones
    """
    return combined['run_count'] - combined['error_count']


SWEEP_SUMMARY_TABLE = FoldTable(
    name='sweep_summary',
    figures={
        'sweep_id': FigureDeclaration(Reduction.FIRST_ROW),
        'started': FigureDeclaration(Reduction.SPAN_START, source=run_start),
        'duration_s': FigureDeclaration(Reduction.DERIVE),
        'run_count': FigureDeclaration(Reduction.COUNT_DISTINCT, source='run_id'),
        'ok_count': FigureDeclaration(Reduction.DERIVE),
        'error_count': FigureDeclaration(Reduction.COUNT_DISTINCT, source=errored_run),
        'decision_logic_type': FigureDeclaration(Reduction.FIRST_ROW),
        'decision_version': FigureDeclaration(Reduction.FIRST_ROW),
        'base_config': FigureDeclaration(Reduction.FIRST_ROW, source=base_config),
        'symbols': FigureDeclaration(Reduction.FIRST_ROW),
        'objective': FigureDeclaration(Reduction.FIRST_ROW, source='sweep_objective',
                                       empty_value=''),
        # A sweep that recorded no direction maximizes — the default the sweep itself applies
        'maximize': FigureDeclaration(Reduction.FIRST_ROW, source='sweep_maximize',
                                      empty_value=True),
        # The ledger writes each run's value through `joined_distinct`, so a member never carries
        # a space the union's strip could remove
        'features_beyond_venue': FigureDeclaration(Reduction.UNION),
    },
    derivations={'duration_s': sweep_duration, 'ok_count': runs_without_error},
    order=GroupOrder.SORTED,
    grain=('sweep_id',),
    row_model=RunResultRow,
    served_as=SweepSummary,
)
