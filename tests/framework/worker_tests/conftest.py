"""
FiniexTestingIDE - Worker Parameter Tests
Shared fixtures for parameter validation testing

No data dependencies. No tick loop. No bars.
Only mock logger and config dicts.
"""

from unittest.mock import MagicMock

import pytest

from python.framework.decision_logic.core.aggressive_trend import AggressiveTrend
from python.framework.decision_logic.core.test_probes.deterministic_probe import (
    DeterministicProbe,
)
from python.framework.decision_logic.core.simple_consensus import SimpleConsensus
from python.framework.workers.core.test_probes.sample_probe_worker import (
    SampleProbeWorker,
)
from python.framework.workers.core.test_probes.heavy_rsi_worker import HeavyRsiWorker
from python.framework.workers.core.bollinger_worker import BollingerWorker
from python.framework.workers.core.ma_trend_worker import MaTrendWorker
from python.framework.workers.core.macd_worker import MacdWorker
from python.framework.workers.core.obv_worker import ObvWorker

# ============================================
# Worker & Logic Imports
# ============================================
from python.framework.workers.core.rsi_worker import RsiWorker

# ============================================
# All CORE workers with schemas
# ============================================
ALL_WORKERS = [
    RsiWorker,
    BollingerWorker,
    MaTrendWorker,
    MacdWorker,
    ObvWorker,
    HeavyRsiWorker,
    SampleProbeWorker,
]

ALL_DECISION_LOGICS = [
    SimpleConsensus,
    AggressiveTrend,
    DeterministicProbe,
]

ALL_COMPONENTS = ALL_WORKERS + ALL_DECISION_LOGICS


# ============================================
# Fixtures
# ============================================

@pytest.fixture(scope='session')
def mock_logger():
    """Minimal mock logger for factory and worker instantiation."""
    logger = MagicMock()
    logger.debug = MagicMock()
    logger.info = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    return logger
