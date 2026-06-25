"""
Window Materializer
===================
Turns a WindowSet into runnable scenarios — the single home for the cross-cutting plumbing
that used to live in two places (the blocks saver and the in-memory profile loader):
role assignment (#367), quote-currency balance seeding (#265), scenario naming, and
regime / session passthrough.

Two output flavors share that plumbing:
- `to_scenario_dicts` — scenario dicts for the saved set-JSON (NO per-scenario cascade keys;
  the quote balance is hoisted into `global` by the serializer for the single-symbol save).
- `to_single_scenarios` — in-memory SingleScenario objects for a Profile Run (per-scenario
  balance, because a multi-symbol profile run mixes quote currencies).
"""

import copy
from typing import Any, Dict, List, Optional

from python.framework.types.config_types.robustness_config_types import (
    RobustnessConfig,
    RobustnessRole,
)
from python.framework.types.scenario_types.scenario_generator_types import GenerationStrategy
from python.framework.types.scenario_types.scenario_set_types import SingleScenario
from python.framework.types.scenario_types.window_set_types import GeneratedWindow, WindowSet
from python.scenario.generator.balance_defaults import ensure_quote_balance, resolve_quote_currency
from python.scenario.generator.role_assignment import assign_roles_time_ordered


class WindowMaterializer:
    """Materializes a WindowSet into scenario dicts or SingleScenario objects."""

    def assign_roles(
        self,
        window_set: WindowSet,
        robustness: Optional[RobustnessConfig],
    ) -> Optional[List[RobustnessRole]]:
        """
        Assign time-ordered IS/OOS roles to a window set (#367), if robustness is enabled.

        Args:
            window_set: The window set to label
            robustness: Robustness config (None or disabled → no roles)

        Returns:
            Per-window roles (index-aligned) or None when robustness is off
        """
        if robustness is not None and robustness.enabled:
            return assign_roles_time_ordered(window_set.block_count, robustness.oos_split)
        return None

    def _window_name(
        self,
        window_set: WindowSet,
        window: GeneratedWindow,
        position: int,
    ) -> str:
        """
        Build a scenario name for a window (keeps the established 3-part `symbol_mode_NN` form).

        Args:
            window_set: The source window set
            window: The window being named
            position: 1-based position (used by the blocks naming scheme)

        Returns:
            Scenario name string
        """
        if window_set.strategy == GenerationStrategy.BLOCKS:
            return f"{window_set.symbol}_{window_set.strategy.value}_{position:02d}"
        mode_short = 'vol' if window_set.mode == 'volatility_split' else 'cont'
        return f"{window_set.symbol}_{mode_short}_{window.block_index:02d}"

    def to_scenario_dicts(
        self,
        window_set: WindowSet,
        robustness: Optional[RobustnessConfig] = None,
    ) -> List[Dict[str, Any]]:
        """
        Build scenario dicts for a saved scenario-set JSON.

        Cascade-capable keys (strategy / execution / trade_simulator) are NOT emitted per
        scenario — they live set-wide in `global` only (the generated set carries no
        per-scenario cascade keys).

        Args:
            window_set: The window set to materialize
            robustness: Optional robustness mode (adds time-ordered IS/OOS roles)

        Returns:
            List of scenario dicts
        """
        roles = self.assign_roles(window_set, robustness)
        is_blocks = window_set.strategy == GenerationStrategy.BLOCKS

        dicts = []
        for i, window in enumerate(window_set.windows, 1):
            name = self._window_name(window_set, window, i)
            role = roles[i - 1].value if roles else None

            # Blocks strategy: max_ticks = None (time-based only)
            effective_max_ticks = None if is_blocks else window.estimated_ticks
            # estimated_ticks 0 → time-based → None
            if effective_max_ticks == 0:
                effective_max_ticks = None

            scenario: Dict[str, Any] = {
                'name': name,
                'symbol': window_set.symbol,
                'data_broker_type': window_set.broker_type,
                'start_date': window.start_time.isoformat(),
                'end_date': window.end_time.isoformat(),
                'max_ticks': effective_max_ticks,  # None → null in JSON
                'data_mode': 'realistic',
                'enabled': True,
            }
            if role is not None:
                scenario['role'] = role
            dicts.append(scenario)

        return dicts

    def to_single_scenarios(
        self,
        window_set: WindowSet,
        *,
        global_strategy: Dict[str, Any],
        global_execution: Dict[str, Any],
        merged_trade_simulator: Dict[str, Any],
        global_stress: Dict[str, Any],
        global_order_guard: Dict[str, Any],
        robustness: Optional[RobustnessConfig],
        start_index: int,
    ) -> List[SingleScenario]:
        """
        Build in-memory SingleScenario objects for a Profile Run.

        Args:
            window_set: The window set to materialize
            global_strategy: Cascaded global strategy_config
            global_execution: Cascaded global execution_config
            merged_trade_simulator: Cascaded trade_simulator_config (app → global)
            global_stress: Global stress_test_config (may be empty)
            global_order_guard: Global order_guard config (may be empty)
            robustness: Optional robustness mode (adds time-ordered IS/OOS roles)
            start_index: Global scenario index to start numbering from

        Returns:
            List of SingleScenario (index-continuous from start_index)
        """
        roles = self.assign_roles(window_set, robustness)

        # Resolve the quote currency once per set (authoritative, broker config) — every
        # scenario gets the symbol's quote balance so a profile run validates out of the box.
        quote_currency = resolve_quote_currency(window_set.symbol, window_set.broker_type)

        scenarios = []
        for local_index, window in enumerate(window_set.windows):
            name = self._window_name(window_set, window, local_index + 1)

            scenario = SingleScenario(
                name=name,
                scenario_index=start_index + local_index,
                symbol=window_set.symbol,
                data_broker_type=window_set.broker_type,
                start_date=window.start_time,
                end_date=window.end_time,
                data_mode='realistic',
                max_ticks=None,
                strategy_config=copy.deepcopy(global_strategy),
                execution_config=copy.deepcopy(global_execution),
                # Seed the symbol's quote-currency balance so a profile run validates out of
                # the box (per scenario — a multi-symbol profile run mixes quote currencies).
                trade_simulator_config=ensure_quote_balance(
                    copy.deepcopy(merged_trade_simulator), quote_currency),
                stress_test_config=copy.deepcopy(global_stress) if global_stress else None,
                order_guard_config=copy.deepcopy(global_order_guard) if global_order_guard else None,
                is_profile_run=True,
                role=roles[local_index] if roles else RobustnessRole.UNASSIGNED,
                # Regime/session carried from the source window → the robustness regime breakdown.
                regime=window.regime.value,
                session=window.session.value,
            )
            scenarios.append(scenario)

        return scenarios
