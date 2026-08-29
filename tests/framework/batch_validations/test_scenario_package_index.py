"""
Scenario Package Index Tests.

The data packages are keyed by the scenario's OWN index — assigned once at config load
(`scenario_config_loader.py`) and used to fill the dict in `shared_data_preparator.py`. The
quality validator receives the FILTERED scenario list (`mount_preparer.py` → `_valid(scenarios)`),
so the position in that list stops matching the index the moment one scenario is excluded.

Keying by the loop position silently pairs a scenario with a NEIGHBOUR's tick data — the
validation then passes or fails against data the scenario never runs on. These tests pin the
lookup to the index, and pin the missing-package case to a hard error: after the fix a hole can
only mean the preparator and the validator disagree, which is framework logic, not operator
config (§33).
"""

from datetime import datetime, timezone

import pytest

from python.framework.exceptions.mount_errors import ScenarioPackageMissingError
from python.framework.types.scenario_types.scenario_set_types import SingleScenario
from python.framework.types.validation_types import (
    Severity,
    ValidationDomain,
    ValidationFinding,
    ValidationResult,
)
from python.framework.validators.scenario_data_validator import ScenarioDataValidator

_DT = datetime(2025, 10, 13, tzinfo=timezone.utc)


def _scenario(name: str, index: int) -> SingleScenario:
    return SingleScenario(name=name, scenario_index=index, symbol='BTCUSD',
                          data_broker_type='kraken_spot', start_date=_DT)


def _reject(scenario: SingleScenario) -> None:
    """Exclude a scenario the way an earlier phase does (§33)."""
    scenario.validation_result.append(ValidationResult(scenario.name, [ValidationFinding(
        severity=Severity.ERROR, check='algo_clock', domain=ValidationDomain.ALGO,
        message='wall-clock read in decision logic', scope=scenario.name)]))


class _RecordingValidator(ScenarioDataValidator):
    """Records which package each scenario was validated against, and validates nothing."""

    def __init__(self):
        self.seen = {}

    def _validate_single_scenario(self, scenario, scenario_package, requirements_map):
        self.seen[scenario.name] = scenario_package
        return ValidationResult(scenario.name)


class TestPackageIsKeyedByTheScenarioIndex:
    def test_each_scenario_gets_its_own_package_after_an_exclusion(self):
        """s0 is excluded, so position 0 of the filtered list is s1 — it must still get pkg1."""
        s0, s1, s2 = _scenario('s0', 0), _scenario('s1', 1), _scenario('s2', 2)
        _reject(s0)
        packages = {1: 'pkg1', 2: 'pkg2'}          # s0 never got one — it was excluded

        validator = _RecordingValidator()
        validator.validate_loaded_data(
            scenarios=[s1, s2], scenario_packages=packages, requirements_map=None)

        assert validator.seen == {'s1': 'pkg1', 's2': 'pkg2'}

    def test_no_exclusion_is_unaffected(self):
        """The common case where position and index coincide keeps working."""
        s0, s1 = _scenario('s0', 0), _scenario('s1', 1)
        validator = _RecordingValidator()
        validator.validate_loaded_data(
            scenarios=[s0, s1], scenario_packages={0: 'pkg0', 1: 'pkg1'}, requirements_map=None)

        assert validator.seen == {'s0': 'pkg0', 's1': 'pkg1'}


class TestAMissingPackageIsAnInconsistency:
    def test_it_raises_rather_than_skipping_silently(self):
        """A hole can no longer be explained by an exclusion, so it must not be swallowed."""
        scenario = _scenario('s7', 7)
        validator = _RecordingValidator()

        with pytest.raises(ScenarioPackageMissingError, match='s7'):
            validator.validate_loaded_data(
                scenarios=[scenario], scenario_packages={0: 'pkg0'}, requirements_map=None)

        assert validator.seen == {}
