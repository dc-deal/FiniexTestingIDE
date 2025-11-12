from dataclasses import dataclass
from typing import Dict, List

from python.framework.trading_env.broker_config import BrokerType
from python.framework.types.process_data_types import ProcessResult
from python.framework.types.scenario_set_types import BrokerScenarioInfo


@dataclass
class BatchExecutionSummary:
    """
    Summary of batch execution results.
    Broker config loaded once in main process
    Used by BrokerSummary for report generation (no redundant loading)
    broker_config: Any = None  # BrokerConfig instance
    """
    success: bool
    scenarios_count: int
    summary_execution_time: float
    scenario_list:  List[ProcessResult] = None
    broker_scenario_map: Dict[BrokerType, BrokerScenarioInfo] = None
