"""
FiniexTestingIDE - Market Compatibility Tests — Shared Fixtures

Fixtures for validating that workers correctly declare their required
activity metric and that the pre-flight validator rejects incompatible
scenarios without reaching subprocess execution.
"""

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from python.configuration.market_config_manager import MarketConfigManager
from python.framework.factory.worker_factory import WorkerFactory
from python.framework.types.scenario_types.scenario_set_types import SingleScenario
from python.framework.validators.scenario_data_validator import ScenarioDataValidator


@pytest.fixture(scope="session")
def mock_logger():
    """Minimal logger mock for validator and factory."""
    logger = MagicMock()
    logger.debug = MagicMock()
    logger.info = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    return logger


@pytest.fixture(scope="session")
def market_config_manager():
    """Real market config manager — reads configs/market_config.json."""
    return MarketConfigManager()


@pytest.fixture(scope="session")
def worker_factory(mock_logger):
    """Worker factory with all CORE workers registered."""
    return WorkerFactory(logger=mock_logger, strict_parameter_validation=False)


@pytest.fixture
def app_config():
    """Minimal app_config mock for ScenarioDataValidator."""
    config = MagicMock()
    config.get_warmup_quality_mode = MagicMock(return_value='standard')
    config.get_allowed_gap_categories = MagicMock(return_value=['seamless'])
    return config


@pytest.fixture
def validator(app_config, mock_logger):
    """ScenarioDataValidator with empty coverage reports (not needed for market checks)."""
    return ScenarioDataValidator(
        data_coverage_reports={},
        app_config=app_config,
        logger=mock_logger,
    )


def make_scenario(
    name: str,
    data_broker_type: str,
    worker_instances: dict,
) -> SingleScenario:
    """
    Build a minimal SingleScenario for validator input.

    Args:
        name: Scenario name
        data_broker_type: Broker type (e.g. 'mt5', 'kraken_spot')
        worker_instances: Dict mapping instance name to worker type string

    Returns:
        SingleScenario with minimal valid configuration
    """
    return SingleScenario(
        name=name,
        scenario_index=0,
        symbol='TEST',
        data_broker_type=data_broker_type,
        start_date=datetime(2026, 1, 1, tzinfo=timezone.utc),
        end_date=datetime(2026, 1, 2, tzinfo=timezone.utc),
        strategy_config={'worker_instances': worker_instances},
    )
