"""
Optimization analysis (#390) — rank + sensitivity over the run-results ledger.

Pure calculation over typed `RunResultRow`s (no verdicts → analyzer, not validator, per the
reporting "no decisions in reports" rule). `rank` orders combinations by the objective;
`sensitivity` is the one-factor marginal-effect view (which parameter moves the objective
most). The sensitivity is OFAT — it ignores interactions and makes no significance claim;
#31 later swaps the spread for a variance / ANOVA importance over the same rows.
"""

from dataclasses import dataclass
from typing import Dict, List, Optional

from python.framework.types.api.report_types import RunResultRow


@dataclass
class ParamSensitivity:
    """One swept parameter's marginal effect on the objective."""
    param: str
    influence: float                # spread of the per-level mean objective (max - min)
    level_means: Dict[str, float]   # level value (as string) → mean objective at that level


def rank(
    rows: List[RunResultRow],
    objective: str,
    maximize: bool = True,
    objective_currency: Optional[str] = None,
) -> List[RunResultRow]:
    """
    Rank combinations by the objective.

    Args:
        rows: Ledger rows (one per run × currency)
        objective: The RunResultRow KPI field to rank by
        maximize: True → best first is highest; False → lowest (e.g. max_drawdown)
        objective_currency: Restrict to this currency (required when > 1 currency present)

    Returns:
        The rows sorted by the objective (stable tie-break by run_id), best first
    """
    scoped = _scope(rows, objective, objective_currency)
    if not scoped:
        return []
    # Two stable passes: secondary key (run_id asc) first, then primary (objective) — so
    # equal-objective rows keep run_id order → deterministic ranking (pairs with #368).
    scoped = sorted(scoped, key=lambda r: r.run_id)
    scoped = sorted(scoped, key=lambda r: getattr(r, objective), reverse=maximize)
    return scoped


def sensitivity(
    rows: List[RunResultRow],
    objective: str,
    objective_currency: Optional[str] = None,
) -> List[ParamSensitivity]:
    """
    One-factor marginal-effect sensitivity per swept parameter.

    Args:
        rows: Ledger rows (only rows carrying sweep_params contribute)
        objective: The RunResultRow KPI field to measure
        objective_currency: Restrict to this currency

    Returns:
        Per-parameter sensitivity, ranked by influence (descending)
    """
    scoped = [r for r in _scope(rows, objective, objective_currency) if r.sweep_params]
    if not scoped:
        return []

    # param → level (string) → list of objective values
    param_levels: Dict[str, Dict[str, List[float]]] = {}
    for row in scoped:
        objective_value = float(getattr(row, objective))
        for param, level in row.sweep_params.items():
            param_levels.setdefault(param, {}).setdefault(str(level), []).append(objective_value)

    result: List[ParamSensitivity] = []
    for param, levels in param_levels.items():
        if len(levels) < 2:     # only one level seen → not actually swept
            continue
        means = {level: sum(values) / len(values) for level, values in levels.items()}
        influence = max(means.values()) - min(means.values())
        result.append(ParamSensitivity(param=param, influence=influence, level_means=means))

    result.sort(key=lambda s: s.influence, reverse=True)
    return result


def _scope(
    rows: List[RunResultRow], objective: str, objective_currency: Optional[str]
) -> List[RunResultRow]:
    """
    Validate the objective + restrict to the evaluable rows.

    Error-flagged rows (status != 'ok') are excluded from the evaluation everywhere — they are
    recorded in the ledger but never rank or contribute to sensitivity (#1).
    """
    if objective not in RunResultRow.model_fields:
        raise ValueError(
            f"Unknown objective '{objective}'. Available: {sorted(RunResultRow.model_fields)}")
    scoped = [r for r in rows if r.status == 'ok']
    if objective_currency is not None:
        scoped = [r for r in scoped if r.currency == objective_currency]
    return scoped
