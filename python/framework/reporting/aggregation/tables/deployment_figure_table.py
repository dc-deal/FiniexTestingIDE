"""
The deployment summary's fold table (#497, #557) — one session of a deployment per row.

A deployment's line in the list folds the sessions of one account currency. Which sessions those
are — booked ones per currency, or the ones that booked nothing — is the family's choice
(`deployment_history_builder.summarize_deployments`); how they fold is declared here.
"""

from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence

from python.framework.types.aggregation_types import (
    FigureDeclaration,
    FoldTable,
    GroupOrder,
    Reduction,
)
from python.framework.types.api.report_types import DeploymentSessionRow, DeploymentSummary
from python.framework.utils.time_utils import ensure_utc_aware


def _starts(rows: Sequence[Any]) -> List[datetime]:
    """
    The sessions' starts, where they have one.

    Args:
        rows: The sessions

    Returns:
        Their starts, in session order
    """
    return [row.started for row in rows if row.started is not None]


def earliest_start(combined: Dict[str, Any], rows: Sequence[Any]) -> Optional[datetime]:
    """
    The earliest session start — compared as UTC, served as recorded.

    Earliest rather than first: the sessions without booked figures take their starts from two
    sources, a ledger row and the run index, and the two do not agree on a time zone.

    Args:
        combined: The figures being built
        rows: The sessions

    Returns:
        The earliest start, or None when no session has one
    """
    starts = _starts(rows)
    return min(starts, key=ensure_utc_aware) if starts else None


def latest_start(combined: Dict[str, Any], rows: Sequence[Any]) -> Optional[datetime]:
    """
    The latest session start — compared as UTC, served as recorded.

    Args:
        combined: The figures being built
        rows: The sessions

    Returns:
        The latest start, or None when no session has one
    """
    starts = _starts(rows)
    return max(starts, key=ensure_utc_aware) if starts else None


DEPLOYMENT_SUMMARY_TABLE = FoldTable(
    name='deployment_summary',
    figures={
        'sessions': FigureDeclaration(Reduction.COUNT),
        'first_started': FigureDeclaration(Reduction.DERIVE),
        'last_started': FigureDeclaration(Reduction.DERIVE),
        # None on the line of the sessions that booked nothing: no figure was measured, and a sum
        # over them would claim a zero
        'net_pnl': FigureDeclaration(Reduction.SUM_COMPLETE),
        # The deepest by magnitude, never a sum: each live row carries the RUNNING decline against
        # the inherited peak, so adding them counts one decline once per session that was still
        # inside it. Over the sessions that measured one: a session without a drawdown is
        # unknown, not a zero.
        'max_drawdown': FigureDeclaration(Reduction.MAX_ABS),
        'max_drawdown_pct': FigureDeclaration(Reduction.COMPANION, companion_of='max_drawdown'),
        'currency': FigureDeclaration(Reduction.FIRST_ROW),
        'bot': FigureDeclaration(Reduction.FIRST_ROW),
        'bot_id': FigureDeclaration(Reduction.FIRST_ROW),
        'longest_gap_hours': FigureDeclaration(Reduction.MAX, source='gap_hours'),
        'orders_to': FigureDeclaration(Reduction.DISTINCT),
    },
    derivations={'first_started': earliest_start, 'last_started': latest_start},
    order=GroupOrder.FIRST_SEEN,
    row_model=DeploymentSessionRow,
    served_as=DeploymentSummary,
    outside_the_fold={
        'deployment_id': 'the group: the family folds one deployment at a time',
        'sessions_without_figures': 'an input: counted over the whole deployment, every currency',
        'sessions_failed': 'an input: counted over the whole deployment, every currency',
        'changed': "a copy: whether the deployment's advisory or code summary exists",
        'configuration_changed': "a copy: whether the deployment's advisory exists",
        'code_summary': "a copy: the deployment's code summary",
    },
)
