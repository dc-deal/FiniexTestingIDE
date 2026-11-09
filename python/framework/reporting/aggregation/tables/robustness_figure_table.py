"""
The robustness report's fold tables (#557) — one window's metric per row.

The metric's distribution over every window, and its aggregate per window role. The means add
exactly (`math.fsum`), as `statistics.fmean` always did.
"""

import statistics
from typing import Any, Dict, Sequence

from python.framework.types.aggregation_types import (
    Addition,
    FigureDeclaration,
    FoldTable,
    Reduction,
)
from python.framework.types.api.report_types import (
    RobustnessDistribution,
    RobustnessRoleAggregate,
    RobustnessWindowRow,
)


def profitable_window(row: Any) -> int:
    """
    Whether a window was profitable — one count each.

    Args:
        row: A robustness window row

    Returns:
        1 when it was, else 0
    """
    return 1 if row.profitable else 0


def share_profitable(combined: Dict[str, Any], rows: Sequence[Any]) -> float:
    """
    The share of the windows that were profitable.

    Args:
        combined: The figures being built, the counts already folded
        rows: The windows

    Returns:
        The share in percent; 0.0 over no window, where the family serves no aggregate
    """
    count = combined['window_count']
    return combined['profitable_windows'] / count * 100 if count else 0.0


def median_metric(combined: Dict[str, Any], rows: Sequence[Any]) -> float:
    """
    The windows' median metric.

    Args:
        combined: The figures being built
        rows: The windows

    Returns:
        The median; 0.0 over no window, where the family serves no aggregate
    """
    return statistics.median([row.metric_value for row in rows]) if rows else 0.0


def metric_spread(combined: Dict[str, Any], rows: Sequence[Any]) -> float:
    """
    The windows' population standard deviation of the metric.

    Args:
        combined: The figures being built
        rows: The windows

    Returns:
        The deviation; 0.0 for a single window
    """
    values = [row.metric_value for row in rows]
    return statistics.pstdev(values) if len(values) > 1 else 0.0


def coefficient_of_variation(combined: Dict[str, Any], rows: Sequence[Any]) -> float:
    """
    The metric's spread over its mean's magnitude.

    Args:
        combined: The figures being built, the mean and the spread already derived
        rows: The windows

    Returns:
        The coefficient; 0.0 for a mean of 0
    """
    mean = combined['mean']
    return (combined['std'] / abs(mean)) if mean else 0.0


ROBUSTNESS_DISTRIBUTION_TABLE = FoldTable(
    name='robustness_distribution',
    figures={
        'window_count': FigureDeclaration(Reduction.COUNT),
        'profitable_windows': FigureDeclaration(Reduction.SUM, source=profitable_window,
                                                empty_value=0),
        'pct_profitable': FigureDeclaration(Reduction.DERIVE),
        'mean': FigureDeclaration(Reduction.MEAN, source='metric_value', addition=Addition.EXACT),
        'median': FigureDeclaration(Reduction.DERIVE),
        'std': FigureDeclaration(Reduction.DERIVE),
        # The best and the worst window — the first one among equals
        'best_value': FigureDeclaration(Reduction.MAX, source='metric_value'),
        'best_window': FigureDeclaration(Reduction.COMPANION, companion_of='best_value',
                                         source='name'),
        'worst_value': FigureDeclaration(Reduction.MIN, source='metric_value'),
        'worst_window': FigureDeclaration(Reduction.COMPANION, companion_of='worst_value',
                                          source='name'),
        'coefficient_of_variation': FigureDeclaration(Reduction.DERIVE),
    },
    derivations={
        'pct_profitable': share_profitable,
        'median': median_metric,
        'std': metric_spread,
        'coefficient_of_variation': coefficient_of_variation,
    },
    row_model=RobustnessWindowRow,
    served_as=RobustnessDistribution,
    outside_the_fold={'profitable_windows': 'folded, not served: the count behind the share'},
)

ROBUSTNESS_ROLE_TABLE = FoldTable(
    name='robustness_role',
    figures={
        # The family folds the windows of one role
        'role': FigureDeclaration(Reduction.FIRST_ROW),
        'window_count': FigureDeclaration(Reduction.COUNT),
        'profitable_windows': FigureDeclaration(Reduction.SUM, source=profitable_window,
                                                empty_value=0),
        'mean_metric': FigureDeclaration(Reduction.MEAN, source='metric_value',
                                         addition=Addition.EXACT),
        'median_metric': FigureDeclaration(Reduction.DERIVE),
        'pct_profitable': FigureDeclaration(Reduction.DERIVE),
    },
    derivations={'median_metric': median_metric, 'pct_profitable': share_profitable},
    row_model=RobustnessWindowRow,
    served_as=RobustnessRoleAggregate,
    outside_the_fold={'profitable_windows': 'folded, not served: the count behind the share'},
)
