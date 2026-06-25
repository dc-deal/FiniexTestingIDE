"""
Robustness Validation Tests (#367).

Multi-window + IS/OOS validation, sim-only. Covers: the time-ordered role policy, the generator
`to_scenario_dict` cleanliness, the parameter-constancy guard, the RobustnessConfig schema, the
DERIVE builder (distribution / IS-OOS / WFE / regime / disposition) against a REAL
BatchExecutionSummary, and the PostRunValidator verdict (OVERFIT / drift / low-N / trust gate).
"""
import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
from pydantic import ValidationError

from python.framework.reporting.builders.robustness_report_builder import build_robustness_report_from_batch
from python.framework.types.batch_execution_types import BatchExecutionSummary
from python.framework.types.config_types.robustness_config_types import (
    RobustnessConfig, RobustnessMetric, RobustnessRole)
from python.framework.types.process_data_types import (
    BlockBoundaryReport, ProcessResult, ProcessTickLoopResult)
from python.framework.types.portfolio_types.portfolio_aggregation_types import PortfolioStats
from python.framework.types.scenario_types.scenario_set_types import SingleScenario
from python.framework.types.trading_env_types.broker_types import BrokerType
from python.framework.validators.post_run_validator import PostRunValidator
from python.framework.validators.scenario_validator import ScenarioValidator
from python.scenario.generator.role_assignment import assign_roles_time_ordered
from python.scenario.scenario_config_loader import ScenarioConfigLoader

_DT = datetime(2026, 2, 10, tzinfo=timezone.utc)


# ─────────────────────────────────────────────────────────────────────────────
# Real-type batch helpers
# ─────────────────────────────────────────────────────────────────────────────

def _stats(net: float, currency: str = 'USD') -> PortfolioStats:
    """A real PortfolioStats whose net_profit equals `net` (total_profit − total_loss)."""
    return PortfolioStats(
        broker_type=BrokerType.KRAKEN_SPOT,
        total_trades=1, total_long_trades=1, total_short_trades=0,
        winning_trades=1 if net > 0 else 0, losing_trades=0 if net > 0 else 1,
        total_profit=net if net > 0 else 0.0, total_loss=0.0 if net > 0 else -net,
        max_drawdown=0.0, max_equity=0.0, win_rate=1.0 if net > 0 else 0.0,
        profit_factor=0.0, total_spread_cost=0.0, total_commission=0.0, total_swap=0.0,
        maker_fee=0.0, taker_fee=0.0, total_fees=0.0,
        currency=currency, broker_name='kraken', current_conversion_rate=1.0,
        current_balance=0.0, initial_balance=0.0, symbol='ETHUSD',
    )


def _result(name: str, idx: int, net: float, boundary: BlockBoundaryReport = None) -> ProcessResult:
    return ProcessResult(
        success=True, scenario_name=name, scenario_index=idx,
        tick_loop_results=ProcessTickLoopResult(
            portfolio_stats=_stats(net), block_boundary_report=boundary))


def _scenario(name, idx, role=RobustnessRole.UNASSIGNED, regime='', strategy=None) -> SingleScenario:
    s = SingleScenario(
        name=name, scenario_index=idx, symbol='ETHUSD', data_broker_type='kraken_spot',
        start_date=_DT, role=role, regime=regime)
    if strategy is not None:
        s.strategy_config = strategy
    return s


def _batch(nets, roles=None, regimes=None, metric=RobustnessMetric.NET_PNL,
           strategies=None, boundaries=None, **cfg) -> BatchExecutionSummary:
    """Build a real batch with one window per net value + the robustness config."""
    roles = roles or [RobustnessRole.UNASSIGNED] * len(nets)
    regimes = regimes or [''] * len(nets)
    strategies = strategies or [{'decision_logic_type': 'CORE/x'}] * len(nets)
    boundaries = boundaries or [None] * len(nets)
    results = [_result(f'ETHUSD_vol_{i:02d}', i, nets[i], boundaries[i]) for i in range(len(nets))]
    scenarios = [
        _scenario(f'ETHUSD_vol_{i:02d}', i, roles[i], regimes[i], strategies[i])
        for i in range(len(nets))]
    return BatchExecutionSummary(
        batch_execution_time=0.0, batch_warmup_time=0.0, batch_tickrun_time=0.0,
        process_result_list=results, single_scenario_list=scenarios,
        robustness_config=RobustnessConfig(enabled=True, metric=metric, **cfg))


def _verdicts(batch) -> dict:
    """Run the PostRunValidator and return {check_name: joined warning text}."""
    PostRunValidator(batch).validate()
    return {vr.scenario_name: '\n'.join(vr.warnings) for vr in batch.batch_validation_result}


# ─────────────────────────────────────────────────────────────────────────────
# Role assignment (time-ordered split policy)
# ─────────────────────────────────────────────────────────────────────────────

class TestAssignRoles:
    def test_split_10_at_30pct(self):
        roles = assign_roles_time_ordered(10, 0.3)
        assert [r.value for r in roles] == ['in_sample'] * 7 + ['out_of_sample'] * 3

    def test_always_one_each_for_two(self):
        assert assign_roles_time_ordered(2, 0.3) == [
            RobustnessRole.IN_SAMPLE, RobustnessRole.OUT_OF_SAMPLE]

    def test_single_window_is_in_sample(self):
        assert assign_roles_time_ordered(1, 0.3) == [RobustnessRole.IN_SAMPLE]

    def test_zero_windows(self):
        assert assign_roles_time_ordered(0, 0.3) == []

    def test_trailing_order(self):
        # OOS is always the trailing fraction, never the lead
        roles = assign_roles_time_ordered(4, 0.5)
        assert roles[0] == RobustnessRole.IN_SAMPLE and roles[-1] == RobustnessRole.OUT_OF_SAMPLE


# Note: the generator window → scenario-dict cleanliness (no cascade keys, role handling)
# moved to WindowMaterializer and is covered by
# tests/data/scenario_generator/test_window_materializer.py (#411).


# ─────────────────────────────────────────────────────────────────────────────
# Parameter constancy guard
# ─────────────────────────────────────────────────────────────────────────────

class TestParameterConstancy:
    def test_constant(self):
        cfg = {'decision_logic_type': 'CORE/x', 'decision_logic_config': {'a': 1}}
        scenarios = [_scenario('w1', 0, strategy=dict(cfg)), _scenario('w2', 1, strategy=dict(cfg))]
        assert ScenarioValidator.check_parameter_constancy(scenarios) == (True, [])

    def test_drift_detected(self):
        a = {'decision_logic_type': 'CORE/x', 'decision_logic_config': {'a': 1}}
        b = {'decision_logic_type': 'CORE/x', 'decision_logic_config': {'a': 2}}
        scenarios = [_scenario('w1', 0, strategy=a), _scenario('w2', 1, strategy=b)]
        constant, drifting = ScenarioValidator.check_parameter_constancy(scenarios)
        assert constant is False and drifting == ['w2']

    def test_single_scenario_constant(self):
        assert ScenarioValidator.check_parameter_constancy([_scenario('w1', 0)]) == (True, [])


# ─────────────────────────────────────────────────────────────────────────────
# RobustnessConfig schema
# ─────────────────────────────────────────────────────────────────────────────

class TestRobustnessConfig:
    def test_defaults(self):
        c = RobustnessConfig()
        assert c.enabled is False and c.metric == RobustnessMetric.EXPECTANCY
        assert c.oos_split == 0.3 and c.min_windows == 3

    def test_rejects_unknown_key(self):
        with pytest.raises(ValidationError):
            RobustnessConfig(enabled=True, bogus=1)

    def test_metric_enum_coercion(self):
        assert RobustnessConfig(metric='net_pnl').metric == RobustnessMetric.NET_PNL

    def test_rejects_invalid_metric(self):
        with pytest.raises(ValidationError):
            RobustnessConfig(metric='sharpe')


# ─────────────────────────────────────────────────────────────────────────────
# DERIVE builder
# ─────────────────────────────────────────────────────────────────────────────

class TestBuildReport:
    def test_disabled_is_empty(self):
        b = BatchExecutionSummary(
            batch_execution_time=0.0, batch_warmup_time=0.0, batch_tickrun_time=0.0)
        r = build_robustness_report_from_batch(b)
        assert r.enabled is False and r.windows == []

    def test_distribution(self):
        r = build_robustness_report_from_batch(_batch([10.0, 0.0, 0.0, -5.0]))
        d = r.distribution
        assert d.window_count == 4
        assert d.pct_profitable == 25.0          # only the +10 window
        assert d.best_value == 10.0 and d.worst_value == -5.0
        assert d.mean == pytest.approx(1.25)

    def test_in_out_of_sample_and_wfe_overfit(self):
        roles = [RobustnessRole.IN_SAMPLE, RobustnessRole.IN_SAMPLE,
                 RobustnessRole.OUT_OF_SAMPLE, RobustnessRole.OUT_OF_SAMPLE]
        r = build_robustness_report_from_batch(_batch([10.0, 10.0, 1.0, 1.0], roles=roles))
        assert r.in_sample.mean_metric == 10.0 and r.out_of_sample.mean_metric == 1.0
        assert r.walk_forward_efficiency == pytest.approx(0.1)

    def test_wfe_robust(self):
        roles = [RobustnessRole.IN_SAMPLE, RobustnessRole.OUT_OF_SAMPLE]
        r = build_robustness_report_from_batch(_batch([10.0, 9.0], roles=roles))
        assert r.walk_forward_efficiency == pytest.approx(0.9)

    def test_wfe_undefined_when_is_not_profitable(self):
        roles = [RobustnessRole.IN_SAMPLE, RobustnessRole.OUT_OF_SAMPLE]
        r = build_robustness_report_from_batch(_batch([-5.0, 1.0], roles=roles))
        assert r.walk_forward_efficiency is None

    def test_regime_breakdown(self):
        regimes = ['high', 'low', 'low']
        r = build_robustness_report_from_batch(_batch([2.0, 4.0, 6.0], regimes=regimes))
        by = {row.regime: row for row in r.regime_breakdown}
        assert by['low'].window_count == 2 and by['low'].mean_metric == pytest.approx(5.0)
        assert by['high'].window_count == 1

    def test_no_regime_breakdown_without_regimes(self):
        assert build_robustness_report_from_batch(_batch([1.0, 2.0])).regime_breakdown == []

    def test_param_drift_flagged(self):
        strategies = [{'decision_logic_config': {'a': 1}}, {'decision_logic_config': {'a': 2}}]
        r = build_robustness_report_from_batch(_batch([1.0, 2.0], strategies=strategies))
        assert r.params_constant is False and r.drifting_windows == ['ETHUSD_vol_01']

    def test_disposition_copied(self):
        boundary = BlockBoundaryReport(
            force_closed_trades=1, force_closed_pnl=40.0,
            natural_closed_trades=1, natural_closed_pnl=10.0, discarded_pending_orders=0)
        r = build_robustness_report_from_batch(_batch([5.0, 5.0], boundaries=[boundary, None]))
        assert r.disposition_pct == pytest.approx(80.0)   # 40 / (40+10)


# ─────────────────────────────────────────────────────────────────────────────
# PostRunValidator verdict
# ─────────────────────────────────────────────────────────────────────────────

class TestPostRunVerdict:
    _ROLES_4 = [RobustnessRole.IN_SAMPLE, RobustnessRole.IN_SAMPLE,
                RobustnessRole.OUT_OF_SAMPLE, RobustnessRole.OUT_OF_SAMPLE]

    def test_disabled_emits_nothing(self):
        b = _batch([10.0, 1.0, 1.0, 1.0], roles=self._ROLES_4)
        b._robustness_config = RobustnessConfig(enabled=False)
        assert 'robustness_overfit' not in _verdicts(b)

    def test_overfit_advisory(self):
        b = _batch([10.0, 10.0, 1.0, 1.0], roles=self._ROLES_4)
        out = _verdicts(b)
        assert 'robustness_overfit' in out and 'OVERFIT' in out['robustness_overfit']

    def test_robust_emits_no_overfit(self):
        b = _batch([10.0, 10.0, 9.0, 9.0], roles=self._ROLES_4)
        assert 'robustness_overfit' not in _verdicts(b)

    def test_param_drift_advisory(self):
        strategies = [{'a': 1}, {'a': 2}, {'a': 1}, {'a': 1}]
        b = _batch([10.0, 10.0, 1.0, 1.0], roles=self._ROLES_4, strategies=strategies)
        assert 'robustness_param_drift' in _verdicts(b)

    def test_low_windows_advisory(self):
        b = _batch([10.0, 1.0], roles=[RobustnessRole.IN_SAMPLE, RobustnessRole.OUT_OF_SAMPLE])
        assert 'robustness_low_windows' in _verdicts(b)

    def test_disposition_suppresses_verdict(self):
        boundary = BlockBoundaryReport(
            force_closed_trades=1, force_closed_pnl=80.0,
            natural_closed_trades=1, natural_closed_pnl=10.0, discarded_pending_orders=0)
        b = _batch([10.0, 10.0, 1.0, 1.0], roles=self._ROLES_4,
                   boundaries=[boundary, None, None, None])
        out = _verdicts(b)
        assert 'robustness_low_trust' in out
        assert 'robustness_overfit' not in out   # suppressed when distortion is high


# ─────────────────────────────────────────────────────────────────────────────
# Loader parsing (role + robustness block from JSON)
# ─────────────────────────────────────────────────────────────────────────────

_FIXTURE = Path(__file__).resolve().parents[2] / 'fixtures' / 'scenario_sets' / 'robustness'


class TestLoaderParsing:
    def test_parses_robustness_block_and_roles(self):
        loaded = ScenarioConfigLoader().load_config(str(_FIXTURE / 'robustness_manual.json'))
        assert loaded.robustness.enabled is True
        assert loaded.robustness.metric == RobustnessMetric.NET_PNL
        roles = [s.role for s in loaded.scenarios]
        assert roles == [RobustnessRole.IN_SAMPLE, RobustnessRole.OUT_OF_SAMPLE]

    def test_invalid_role_rejected(self):
        with pytest.raises(ValueError):
            ScenarioConfigLoader().load_config(str(_FIXTURE / 'robustness_bad_role.json'))
