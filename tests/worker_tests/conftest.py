"""
FiniexTestingIDE - Worker Parameter Tests
Shared fixtures for parameter validation testing

No data dependencies. No tick loop. No bars.
Only mock logger and config dicts.
"""

import pytest
from unittest.mock import MagicMock

from python.framework.types.parameter_types import ParameterDef, REQUIRED
from python.framework.validators.parameter_validator import validate_parameters, apply_defaults

# ============================================
# Worker & Logic Imports
# ============================================
from python.framework.workers.core.rsi_worker import RSIWorker
from python.framework.workers.core.envelope_worker import EnvelopeWorker
from python.framework.workers.core.macd_worker import MACDWorker
from python.framework.workers.core.obv_worker import OBVWorker
from python.framework.workers.core.backtesting.heavy_rsi_worker import HeavyRSIWorker
from python.framework.workers.core.backtesting.backtesting_sample_worker import BacktestingSampleWorker

from python.framework.decision_logic.core.simple_consensus import SimpleConsensus
from python.framework.decision_logic.core.aggressive_trend import AggressiveTrend
from python.framework.decision_logic.core.backtesting.backtesting_deterministic import BacktestingDeterministic


# ============================================
# All CORE workers with schemas
# ============================================
ALL_WORKERS = [
    RSIWorker,
    EnvelopeWorker,
    MACDWorker,
    OBVWorker,
    HeavyRSIWorker,
    BacktestingSampleWorker,
]

ALL_DECISION_LOGICS = [
    SimpleConsensus,
    AggressiveTrend,
    BacktestingDeterministic,
]

ALL_COMPONENTS = ALL_WORKERS + ALL_DECISION_LOGICS


# ============================================
# Fixtures
# ============================================

@pytest.fixture(scope="session")
def mock_logger():
    """Minimal mock logger for factory and worker instantiation."""
    logger = MagicMock()
    logger.debug = MagicMock()
    logger.info = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    return logger
