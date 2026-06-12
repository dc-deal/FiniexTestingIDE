"""
RequirementsCollector — state-snapshot pre-flight integration (#354)

Verifies the centralized batch pre-flight: a decision logic that opts into
persistence and returns a non-serializable snapshot is flagged (→ the scenario
would be excluded via ValidationResult), a clean one passes, an opt-out one is
ignored, and the result is cached per distinct (decision_logic_type, config) — so
a single-logic set is checked once and a mixed set keeps distinct entries.

These run in the main process via the collector's own DecisionLogicFactory, with
test-double logics registered through the public register_logic() — no broker
config, no data, no worker plumbing.
"""

from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from python.framework.batch.requirements_collector import RequirementsCollector
from python.framework.decision_logic.abstract_decision_logic import AbstractDecisionLogic
from python.framework.types.trading_env_types.order_types import OrderType


class _BaseStubLogic(AbstractDecisionLogic):
    """Minimal concrete decision logic — the abstractmethods are never exercised here."""

    @classmethod
    def get_required_order_types(cls, decision_logic_config: Dict[str, Any]) -> List[OrderType]:
        return [OrderType.MARKET]

    def get_required_worker_instances(self) -> Dict[str, str]:
        return {}

    def compute_tick(self, tick, worker_results):
        return None

    def _execute_decision_impl(self, decision, tick):
        return None


class CleanStateLogic(_BaseStubLogic):
    """Opts in, serializable snapshot. Counts instantiations (cache proof)."""
    instances_created = 0

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        type(self).instances_created += 1

    def uses_state_persistence(self) -> bool:
        return True

    def get_state_snapshot(self) -> Dict[str, Any]:
        return {'counter': 1, 'flag': True}


class BrokenStateLogic(_BaseStubLogic):
    """Opts in, NON-serializable snapshot (a set)."""

    def uses_state_persistence(self) -> bool:
        return True

    def get_state_snapshot(self) -> Dict[str, Any]:
        return {'bad': {1, 2, 3}}


class OptOutStateLogic(_BaseStubLogic):
    """Does NOT opt in — its broken snapshot must be ignored."""

    def uses_state_persistence(self) -> bool:
        return False

    def get_state_snapshot(self) -> Dict[str, Any]:
        return {'bad': {1, 2, 3}}


def _scenario(logic_type: str, config: Dict[str, Any] = None) -> SimpleNamespace:
    """Lightweight scenario stand-in — the pre-flight reads only strategy_config."""
    return SimpleNamespace(
        name=f'scenario_{logic_type}',
        strategy_config={
            'decision_logic_type': logic_type,
            'decision_logic_config': config or {},
        },
    )


@pytest.fixture
def collector(logger):
    """A RequirementsCollector with the stub logics registered."""
    c = RequirementsCollector(logger=logger)
    c._decision_logic_factory.register_logic('TEST/clean', CleanStateLogic)
    c._decision_logic_factory.register_logic('TEST/broken', BrokenStateLogic)
    c._decision_logic_factory.register_logic('TEST/optout', OptOutStateLogic)
    return c


class TestStateSnapshotPreflight:
    """RequirementsCollector._state_snapshot_preflight — the centralized #354 check."""

    def test_clean_logic_passes(self, collector):
        assert collector._state_snapshot_preflight(_scenario('TEST/clean')) is None

    def test_broken_logic_flagged(self, collector):
        error = collector._state_snapshot_preflight(_scenario('TEST/broken'))
        assert error is not None
        assert 'bad' in error                 # names the offending key
        assert 'JSON-serializable' in error

    def test_optout_logic_ignored(self, collector):
        # Broken snapshot, but the logic does not opt in → never checked.
        assert collector._state_snapshot_preflight(_scenario('TEST/optout')) is None

    def test_no_logic_type_is_noop(self, collector):
        assert collector._state_snapshot_preflight(SimpleNamespace(strategy_config={})) is None

    def test_cached_per_distinct_logic(self, collector):
        CleanStateLogic.instances_created = 0
        scenario = _scenario('TEST/clean')
        collector._state_snapshot_preflight(scenario)
        collector._state_snapshot_preflight(scenario)   # same (type, config) → cache hit
        assert CleanStateLogic.instances_created == 1

    def test_mixed_set_keeps_distinct_entries(self, collector):
        # A mixed set: clean passes, broken is flagged — independently, both cached.
        assert collector._state_snapshot_preflight(_scenario('TEST/clean')) is None
        assert collector._state_snapshot_preflight(_scenario('TEST/broken')) is not None
        assert len(collector._state_preflight_cache) == 2
