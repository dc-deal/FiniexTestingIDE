"""
WindowMaterializer Tests
========================
Unit tests for the single home of the generator's cross-cutting plumbing: role assignment
(#367), quote-currency balance seeding (#265), naming, and regime/session passthrough.
"""

from datetime import datetime, timezone

import pytest

from python.framework.types.config_types.robustness_config_types import (
    RobustnessConfig,
    RobustnessRole,
)
from python.framework.types.market_types.market_volatility_profile_types import (
    TradingSession,
    VolatilityRegime,
)
from python.framework.types.scenario_types.scenario_generator_types import GenerationStrategy
from python.framework.types.scenario_types.window_set_types import GeneratedWindow, WindowSet
from python.scenario.generator.window_materializer import WindowMaterializer

from conftest import utc

_CASCADE_KEYS = ('strategy_config', 'execution_config', 'trade_simulator_config')


def make_window_set(
    n: int,
    strategy: GenerationStrategy = GenerationStrategy.CONTINUOUS,
    symbol: str = 'ETHUSD',
    broker: str = 'kraken_spot',
) -> WindowSet:
    """Build a WindowSet with n consecutive 6h windows for testing."""
    windows = [
        GeneratedWindow(
            block_index=i,
            start_time=utc(2025, 10, 1 + i),
            end_time=utc(2025, 10, 1 + i, 6),
            regime=VolatilityRegime.HIGH,
            session=TradingSession.LONDON,
            estimated_ticks=1000,
            atr=0.5,
            split_reason='continuous_region',
        )
        for i in range(n)
    ]
    return WindowSet(
        symbol=symbol,
        broker_type=broker,
        strategy=strategy,
        windows=windows,
        generated_at=datetime.now(timezone.utc),
        mode=strategy.value,
    )


# =============================================================================
# ROLE ASSIGNMENT
# =============================================================================

class TestAssignRoles:
    """Tests for time-ordered IS/OOS role assignment."""

    def test_disabled_returns_none(self):
        """Robustness off → no roles."""
        materializer = WindowMaterializer()
        assert materializer.assign_roles(make_window_set(4), None) is None
        assert materializer.assign_roles(
            make_window_set(4), RobustnessConfig(enabled=False)) is None

    def test_enabled_time_ordered(self):
        """Robustness on → time-ordered IS then OOS, correct split."""
        materializer = WindowMaterializer()
        roles = materializer.assign_roles(
            make_window_set(4), RobustnessConfig(enabled=True, oos_split=0.5))
        assert roles == [
            RobustnessRole.IN_SAMPLE, RobustnessRole.IN_SAMPLE,
            RobustnessRole.OUT_OF_SAMPLE, RobustnessRole.OUT_OF_SAMPLE,
        ]


# =============================================================================
# SCENARIO DICTS (save path)
# =============================================================================

class TestToScenarioDicts:
    """Tests for the saved scenario-set dict materialization."""

    def test_no_cascade_keys(self):
        """Generated scenario dicts carry NO per-scenario cascade keys."""
        materializer = WindowMaterializer()
        dicts = materializer.to_scenario_dicts(make_window_set(3))
        for d in dicts:
            assert not any(k in d for k in _CASCADE_KEYS)

    def test_role_present_when_enabled(self):
        """Robustness on → each dict carries a role; off → no role key."""
        materializer = WindowMaterializer()
        with_robust = materializer.to_scenario_dicts(
            make_window_set(4), RobustnessConfig(enabled=True, oos_split=0.5))
        assert [d['role'] for d in with_robust] == [
            'in_sample', 'in_sample', 'out_of_sample', 'out_of_sample']

        without = materializer.to_scenario_dicts(make_window_set(4))
        assert all('role' not in d for d in without)

    def test_blocks_naming_three_part(self):
        """Blocks names are the 3-part symbol_mode_NN form (report-parseable)."""
        materializer = WindowMaterializer()
        dicts = materializer.to_scenario_dicts(
            make_window_set(2, strategy=GenerationStrategy.BLOCKS))
        assert dicts[0]['name'] == 'ETHUSD_blocks_01'
        assert dicts[1]['name'] == 'ETHUSD_blocks_02'


# =============================================================================
# SINGLE SCENARIOS (profile in-memory path)
# =============================================================================

class TestToSingleScenarios:
    """Tests for the in-memory SingleScenario materialization."""

    def _materialize(self, window_set, robustness=None):
        materializer = WindowMaterializer()
        return materializer.to_single_scenarios(
            window_set,
            global_strategy={'worker_instances': {}},
            global_execution={},
            merged_trade_simulator={'balances': {}},
            global_stress={},
            global_order_guard={},
            robustness=robustness,
            start_index=0,
        )

    def test_per_scenario_quote_balance(self):
        """Each scenario gets the symbol's authoritative quote-currency balance (#265)."""
        scenarios = self._materialize(make_window_set(2))  # ETHUSD → USD
        for s in scenarios:
            assert 'USD' in s.trade_simulator_config['balances']

    def test_regime_session_passthrough(self):
        """Regime / session are carried from the source window."""
        scenarios = self._materialize(make_window_set(2))
        for s in scenarios:
            assert s.regime == VolatilityRegime.HIGH.value
            assert s.session == TradingSession.LONDON.value
            assert s.is_profile_run is True

    def test_index_continuity_and_roles(self):
        """scenario_index is continuous from start_index; roles assigned when enabled."""
        scenarios = self._materialize(
            make_window_set(4), RobustnessConfig(enabled=True, oos_split=0.5))
        assert [s.scenario_index for s in scenarios] == [0, 1, 2, 3]
        assert [s.role for s in scenarios] == [
            RobustnessRole.IN_SAMPLE, RobustnessRole.IN_SAMPLE,
            RobustnessRole.OUT_OF_SAMPLE, RobustnessRole.OUT_OF_SAMPLE,
        ]

    def test_start_index_offset(self):
        """start_index offsets the scenario indices (multi-set merge)."""
        materializer = WindowMaterializer()
        scenarios = materializer.to_single_scenarios(
            make_window_set(2),
            global_strategy={}, global_execution={},
            merged_trade_simulator={'balances': {}},
            global_stress={}, global_order_guard={},
            robustness=None, start_index=5,
        )
        assert [s.scenario_index for s in scenarios] == [5, 6]
