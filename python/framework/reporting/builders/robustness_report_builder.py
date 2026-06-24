"""
Robustness report builder (#367) — the multi-window + IS/OOS postprocessor (sim-only).

Pure DERIVE: per window it reuses the existing section builders + `build_run_summary` (never
re-derives P&L / expectancy), groups the windows by IS/OOS role, and computes the distribution
(mean / median / std / % profitable / best / worst / CoV) + Walk-Forward Efficiency. The
ROBUST/OVERFIT verdict is NOT decided here — that is a decision in the PostRunValidator. The
block-splitting disposition is copied in as the trust-gate input for that verdict.
"""
import statistics
from typing import Dict, List, Optional

from python.framework.reporting.builders.block_splitting_report_builder import build_block_splitting_report_from_batch
from python.framework.reporting.builders.execution_stats_report_builder import build_execution_stats_report
from python.framework.reporting.builders.portfolio_report_builder import build_portfolio_report
from python.framework.reporting.builders.run_summary_builder import build_run_summary
from python.framework.reporting.builders.run_unit import RunUnit, run_units_from_batch
from python.framework.reporting.builders.trade_history_report_builder import build_trade_history_report
from python.framework.types.api.report_types import (
    RobustnessDistribution, RobustnessRegimeRow, RobustnessReport, RobustnessRoleAggregate,
    RobustnessWindowRow)
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.config_types.robustness_config_types import RobustnessMetric, RobustnessRole
from python.framework.types.scenario_types.scenario_set_types import SingleScenario
from python.framework.validators.scenario_validator import ScenarioValidator


def build_robustness_report_from_batch(batch: BatchExecutionSummary) -> RobustnessReport:
    """
    Build the robustness report from a finished sim batch.

    Args:
        batch: The completed batch summary (carries the robustness config + scenarios)

    Returns:
        RobustnessReport — empty (enabled=False) when robustness mode is off
    """
    config = batch.robustness_config
    if not config.enabled:
        return RobustnessReport(enabled=False, metric=config.metric.value)

    scenario_by_name: Dict[str, SingleScenario] = {
        s.name: s for s in batch.single_scenario_list}

    rows: List[RobustnessWindowRow] = [
        _window_row(unit, scenario_by_name.get(unit.name), config.metric)
        for unit in run_units_from_batch(batch)
    ]

    distribution = _distribution([r for r in rows])
    in_sample = _role_aggregate(rows, RobustnessRole.IN_SAMPLE)
    out_of_sample = _role_aggregate(rows, RobustnessRole.OUT_OF_SAMPLE)
    wfe = _walk_forward_efficiency(in_sample, out_of_sample)

    params_constant, drifting = ScenarioValidator.check_parameter_constancy(
        batch.single_scenario_list)

    # Trust-gate input — the per-window numbers are artifacts when block-splitting distortion is
    # high. Generator profiles are not needed for the disposition math (only the mode label).
    disposition = build_block_splitting_report_from_batch(batch, []).agg_disposition_pct

    return RobustnessReport(
        enabled=True,
        metric=config.metric.value,
        windows=rows,
        distribution=distribution,
        in_sample=in_sample,
        out_of_sample=out_of_sample,
        walk_forward_efficiency=wfe,
        params_constant=params_constant,
        drifting_windows=drifting,
        disposition_pct=disposition,
        regime_breakdown=_regime_breakdown(rows),
        overfit_wfe_threshold=config.overfit_wfe_threshold,
        robust_wfe_threshold=config.robust_wfe_threshold,
        disposition_trust_pct=config.disposition_trust_pct,
        min_windows=config.min_windows,
    )


def _window_row(
    unit: RunUnit, scenario: Optional[SingleScenario], metric: RobustnessMetric
) -> RobustnessWindowRow:
    """Compose one window's row by reusing the section builders + run-summary (no re-derive)."""
    portfolio = build_portfolio_report([unit])
    trade = build_trade_history_report([unit])
    execution = build_execution_stats_report([unit])
    summary = build_run_summary(portfolio, trade, execution)
    ccy = summary.currencies[0] if summary.currencies else None

    expectancy = ccy.expectancy if ccy else 0.0
    net_pnl = ccy.net_pnl if ccy else 0.0
    metric_value = expectancy if metric == RobustnessMetric.EXPECTANCY else net_pnl

    return RobustnessWindowRow(
        name=unit.name,
        role=scenario.role.value if scenario else RobustnessRole.UNASSIGNED.value,
        regime=scenario.regime if scenario else '',
        session=scenario.session if scenario else '',
        currency=ccy.currency if ccy else '',
        metric_value=metric_value,
        net_pnl=net_pnl,
        expectancy=expectancy,
        total_trades=ccy.total_trades if ccy else 0,
        profitable=metric_value > 0,
    )


def _distribution(rows: List[RobustnessWindowRow]) -> Optional[RobustnessDistribution]:
    """Compute the metric distribution across all windows (None when there are no windows)."""
    if not rows:
        return None
    values = [r.metric_value for r in rows]
    mean = statistics.fmean(values)
    std = statistics.pstdev(values) if len(values) > 1 else 0.0
    best = max(rows, key=lambda r: r.metric_value)
    worst = min(rows, key=lambda r: r.metric_value)
    profitable = sum(1 for r in rows if r.profitable)
    return RobustnessDistribution(
        window_count=len(rows),
        pct_profitable=profitable / len(rows) * 100,
        mean=mean,
        median=statistics.median(values),
        std=std,
        best_value=best.metric_value,
        best_window=best.name,
        worst_value=worst.metric_value,
        worst_window=worst.name,
        coefficient_of_variation=(std / abs(mean)) if mean else 0.0,
    )


def _role_aggregate(
    rows: List[RobustnessWindowRow], role: RobustnessRole
) -> Optional[RobustnessRoleAggregate]:
    """Aggregate the metric over one role's windows (None when no window carries the role)."""
    role_rows = [r for r in rows if r.role == role.value]
    if not role_rows:
        return None
    values = [r.metric_value for r in role_rows]
    profitable = sum(1 for r in role_rows if r.profitable)
    return RobustnessRoleAggregate(
        role=role.value,
        window_count=len(role_rows),
        mean_metric=statistics.fmean(values),
        median_metric=statistics.median(values),
        pct_profitable=profitable / len(role_rows) * 100,
    )


def _walk_forward_efficiency(
    in_sample: Optional[RobustnessRoleAggregate],
    out_of_sample: Optional[RobustnessRoleAggregate],
) -> Optional[float]:
    """OOS mean / IS mean — None when either role is absent or IS mean ≤ 0 (degradation undefined)."""
    if in_sample is None or out_of_sample is None:
        return None
    if in_sample.mean_metric <= 0:
        return None
    return out_of_sample.mean_metric / in_sample.mean_metric


def _regime_breakdown(rows: List[RobustnessWindowRow]) -> List[RobustnessRegimeRow]:
    """Per-regime metric breakdown (Profile Runs only — empty when no window carries a regime)."""
    by_regime: Dict[str, List[RobustnessWindowRow]] = {}
    for row in rows:
        if row.regime:
            by_regime.setdefault(row.regime, []).append(row)
    breakdown: List[RobustnessRegimeRow] = []
    for regime in sorted(by_regime):
        regime_rows = by_regime[regime]
        values = [r.metric_value for r in regime_rows]
        profitable = sum(1 for r in regime_rows if r.profitable)
        breakdown.append(RobustnessRegimeRow(
            regime=regime,
            window_count=len(regime_rows),
            mean_metric=statistics.fmean(values),
            pct_profitable=profitable / len(regime_rows) * 100,
        ))
    return breakdown
