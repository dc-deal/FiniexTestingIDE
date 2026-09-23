"""
Optimization analysis (#390) — rank + sensitivity over the run-results ledger.

Pure calculation over typed `RunResultRow`s (no verdicts → analyzer, not validator, per the
reporting "no decisions in reports" rule). `rank` orders combinations by the objective;
`sensitivity` is the one-factor marginal-effect view (which parameter moves the objective
most). The sensitivity is OFAT — it ignores interactions and makes no significance claim;
#31 later swaps the spread for a variance / ANOVA importance over the same rows.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, get_args

from python.framework.reporting.store.ledger_aggregation import aggregate_ledger_rows
from python.framework.types.api.report_types import RunResultRow, SweepSummary

# A KPI that may be undefined cannot order a ranking: the comparison against None
# raises, and there is no honest position for 'not measured' in a best-first list.
# Derived from the model, so a KPI widened to Optional later is covered without
# touching this module. Today: profit_factor (no losing trade), avg_win_r /
# avg_loss_r (empty R subset), signal_fresh_ratio (no SIGNAL worker).
_UNRANKABLE_OBJECTIVES = frozenset(
    name for name, field in RunResultRow.model_fields.items()
    if type(None) in get_args(field.annotation))


@dataclass
class ParamSensitivity:
    """One swept parameter's marginal effect on the objective."""
    param: str
    influence: float                # spread of the per-level mean objective (max - min)
    level_means: Dict[str, float]   # level value (as string) → mean objective at that level


def summarize_sweeps(rows: List[RunResultRow]) -> List[SweepSummary]:
    """
    Group ledger rows by sweep and summarize each — for the sweep list view.

    Args:
        rows: All ledger rows (non-sweep rows, sweep_id is None, are ignored)

    Returns:
        One SweepSummary per sweep, ordered by sweep_id (timestamp-based → chronological)
    """
    groups: Dict[str, List[RunResultRow]] = {}
    for row in rows:
        if row.sweep_id:
            groups.setdefault(row.sweep_id, []).append(row)

    summaries: List[SweepSummary] = []
    for sweep_id, group in groups.items():
        stamps = sorted(datetime.fromisoformat(r.run_timestamp) for r in group if r.run_timestamp)
        run_ids = {r.run_id for r in group}
        error_ids = {r.run_id for r in group if r.status == 'error'}
        head = group[0]
        summaries.append(SweepSummary(
            sweep_id=sweep_id,
            started=stamps[0] if stamps else None,
            duration_s=(stamps[-1] - stamps[0]).total_seconds() if len(stamps) > 1 else 0.0,
            run_count=len(run_ids),
            ok_count=len(run_ids) - len(error_ids),
            error_count=len(error_ids),
            decision_logic_type=head.decision_logic_type,
            decision_version=head.decision_version,
            base_config=head.scenario_set_name.split('__', 1)[0],
            symbols=head.symbols,
            objective=head.sweep_objective or '',
            maximize=head.sweep_maximize if head.sweep_maximize is not None else True,
        ))

    return sorted(summaries, key=lambda s: s.sweep_id)


@dataclass
class DegenerateRankingAdvisory:
    """
    The ranking's winner made no trade, so the objective is not measuring the strategy.

    Args:
        objective: The KPI the sweep ranked by
        zero_trade_count: How many combinations produced no trade at all
        zero_trade_leaders: The zero-trade rows standing at the TOP of the ranking
        best_trading_row: The best-ranked combination that actually traded, None when none did
        best_trading_rank: That row's 1-based position in the ranking, 0 when there is none
        total_ranked: How many combinations the ranking covers
    """
    objective: str
    zero_trade_count: int
    zero_trade_leaders: List[RunResultRow]
    best_trading_row: Optional[RunResultRow]
    best_trading_rank: int
    total_ranked: int


@dataclass
class MixedLogicVersionAdvisory:
    """
    The ranking spans rows written by different versions of the producing logic.

    Args:
        versions: The versions present, ascending; None reads as "written before row
            versioning existed", which is unknown rather than old
        counts: How many rows carry each of them, in the same order
        unknown_count: Rows whose version is None
    """
    versions: List[Optional[int]]
    counts: List[int]
    unknown_count: int


def mixed_logic_version_advisory(
    rows: List[RunResultRow],
) -> Optional[MixedLogicVersionAdvisory]:
    """
    Detect a ranking built from rows that were not produced by the same logic.

    A ledger column keeps its name while the measure behind it changes — that is what
    happened to the account drawdown (#497), and nothing in a fragment said so. Ranking
    across such a boundary produces a best-first list whose entries answer different
    questions, and it looks exactly like a valid ranking.

    This is an analyzer, not a validator — it returns the facts and renders no verdict.

    Args:
        rows: The ledger rows a ranking is about to be built from

    Returns:
        The advisory, or None when every row carries the same version

    """
    if not rows:
        return None

    tally: Dict[Optional[int], int] = {}
    for row in rows:
        tally[row.logic_version] = tally.get(row.logic_version, 0) + 1
    if len(tally) < 2:
        return None

    # None sorts first: it is the oldest thing present, and it is what a reader has to
    # resolve by hand because no fragment recorded it.
    ordered = sorted(tally, key=lambda v: (v is not None, v))
    return MixedLogicVersionAdvisory(
        versions=ordered,
        counts=[tally[v] for v in ordered],
        unknown_count=tally.get(None, 0),
    )


def degenerate_ranking_advisory(
    ranked: List[RunResultRow],
    objective: str,
    max_leaders: int = 3,
) -> Optional[DegenerateRankingAdvisory]:
    """
    Detect a ranking whose best combination never traded.

    Pardo states the case for drawdown (line 4232): *"minimum drawdown is not enough as a
    sole criterion, since a drawdown of zero occurs when a model has no losing trades and
    possibly no winning trades"*. An honest measure does not fix that — a model that never
    opened a position genuinely has no drawdown, and minimising the measure genuinely
    prefers it. So the condition tested here is the general one and names no KPI: the
    ranking is misleading exactly when its WINNER did nothing, whatever it was ranked by.

    This is an analyzer, not a validator — it returns the facts and renders no verdict.

    Args:
        ranked: The rows in ranking order, best first
        objective: The KPI they were ranked by, carried for the message
        max_leaders: How many of the leading zero-trade rows to carry

    Returns:
        The advisory, or None when the best-ranked combination did trade
    """
    if not ranked or ranked[0].total_trades > 0:
        return None

    leaders = []
    for row in ranked:
        if row.total_trades > 0:
            break
        leaders.append(row)

    best_trading = next(
        ((i, r) for i, r in enumerate(ranked, start=1) if r.total_trades > 0), None)

    return DegenerateRankingAdvisory(
        objective=objective,
        zero_trade_count=sum(1 for r in ranked if r.total_trades == 0),
        zero_trade_leaders=leaders[:max_leaders],
        best_trading_row=best_trading[1] if best_trading else None,
        best_trading_rank=best_trading[0] if best_trading else 0,
        total_ranked=len(ranked),
    )


def rank(
    rows: List[RunResultRow],
    objective: str,
    maximize: bool = True,
    objective_currency: Optional[str] = None,
) -> List[RunResultRow]:
    """
    Rank combinations by the objective.

    Args:
        rows: Raw ledger rows — MANY per run since #537 books one per booking period.
            `_scope` folds them to one per run × currency, which is the shape returned
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
    Validate the objective, restrict to the evaluable rows, and fold the booking periods back
    into one row per combination.

    Error-flagged rows (status != 'ok') are excluded from the evaluation everywhere — they are
    recorded in the ledger but never rank or contribute to sensitivity (#1).

    **The fold is what keeps a ranking a ranking.** Since #537 a run books one row per booking
    period — measured, a simulation scenario averages 3.4 days, so a sweep of 500 combinations
    writes about 1700 rows. Sorted as they are, one candidate would appear several times and the
    top ten would be the ten best DAYS rather than the ten best parameter sets.

    Folding rather than filtering, because there is nothing to filter to: no aggregate row is
    stored beside the periods any more. `aggregate_ledger_rows` rebuilds it from the declared
    reductions — the rates from their summed components, the drawdown trio from the row that
    won it — which is the same figure the stored row used to carry and can no longer drift from
    the periods it came from.
    """
    if objective not in RunResultRow.model_fields:
        raise ValueError(
            f"Unknown objective '{objective}'. Available: {sorted(RunResultRow.model_fields)}")
    if objective in _UNRANKABLE_OBJECTIVES:
        raise ValueError(
            f"Objective '{objective}' can be undefined for a run, so it cannot produce a "
            f"total ranking. Use a KPI that is always measured, e.g. 'expectancy' or 'net_pnl'.")
    scoped = [r for r in rows if r.status == 'ok']
    if objective_currency is not None:
        scoped = [r for r in scoped if r.currency == objective_currency]
    return aggregate_ledger_rows(scoped, by=('run_id', 'currency'))
