"""
FiniexTestingIDE - USER Namespace Tests
Shared fixtures for auto-discovery and hot-reload testing

No data dependencies. No tick loop. No bars.
Uses tmp_path for isolated test modules.
"""

import pytest
from pathlib import Path
from unittest.mock import MagicMock


@pytest.fixture(scope='session')
def mock_logger():
    """Minimal mock logger for factory instantiation."""
    logger = MagicMock()
    logger.debug = MagicMock()
    logger.info = MagicMock()
    logger.warning = MagicMock()
    logger.error = MagicMock()
    return logger


def write_module(directory: Path, filename: str, code: str):
    """
    Write a Python module file to a directory.

    Args:
        directory: Target directory
        filename: File name (e.g. 'my_worker.py')
        code: Python source code
    """
    filepath = directory / filename
    filepath.write_text(code)
    return filepath


# ============================================
# Valid worker source code for test modules
# ============================================

VALID_WORKER_CODE = '''
from typing import Any, Dict, List
from python.framework.types.market_types.market_data_types import Bar, TickData
from python.framework.types.worker_types import WorkerResult, WorkerType
from python.framework.workers.abstract_worker import AbstractWorker


class {class_name}(AbstractWorker):
    """Test worker for USER namespace discovery."""

    @classmethod
    def get_worker_type(cls) -> WorkerType:
        return WorkerType.INDICATOR

    @classmethod
    def get_parameter_schema(cls) -> Dict:
        return {{}}

    def get_warmup_requirements(self) -> Dict[str, int]:
        return self.periods

    def get_required_timeframes(self) -> List[str]:
        return list(self.periods.keys())

    def should_recompute(self, tick: TickData, bar_updated: bool) -> bool:
        return bar_updated

    def compute(self, tick, bar_history, current_bars) -> WorkerResult:
        return WorkerResult(outputs={{
            'value': {marker_value},
        }})
'''

VALID_LOGIC_CODE = '''
from typing import Any, Dict, List, Optional
from python.framework.decision_logic.abstract_decision_logic import AbstractDecisionLogic
from python.framework.types.decision_logic_types import Decision, DecisionLogicAction
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.trading_env_types.order_types import OrderType, OrderResult
from python.framework.types.worker_types import WorkerResult


class {class_name}(AbstractDecisionLogic):
    """Test decision logic for USER namespace discovery."""

    @classmethod
    def get_parameter_schema(cls) -> Dict:
        return {{}}

    @classmethod
    def get_required_order_types(cls, decision_logic_config: Dict[str, Any]) -> List[OrderType]:
        return [OrderType.MARKET]

    def get_required_worker_instances(self) -> Dict[str, str]:
        return {{}}

    def compute(self, tick, worker_results) -> Decision:
        return Decision(
            action=DecisionLogicAction.FLAT,
            outputs={{
                'reason': 'test',
                'price': tick.mid,
            }},
        )

    def _execute_decision_impl(self, decision, tick) -> Optional[OrderResult]:
        return None
'''

NOT_A_WORKER_CODE = '''
class NotAWorker:
    """A class that does NOT inherit from AbstractWorker."""
    pass
'''

SYNTAX_ERROR_CODE = '''
def broken(
    # missing closing paren and colon
'''

IMPORT_ERROR_CODE = '''
from nonexistent_module import SomethingFake

class BadImportWorker:
    pass
'''
