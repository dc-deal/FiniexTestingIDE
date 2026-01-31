from dataclasses import dataclass
from typing import Dict, List

from python.framework.types.broker_types import BrokerType
from python.framework.types.process_data_types import ProcessResult
from python.framework.types.scenario_set_types import BrokerScenarioInfo, SingleScenario


class BatchExecutionSummary:
    """
    Summary of batch execution results.

    Broker config loaded once in main process.
    Used by BrokerSummary for report generation (no redundant loading).
    """

    def __init__(
        self,
        batch_execution_time: float,
        batch_warmup_time: float,
        batch_tickrun_time: float,
        process_result_list: List[ProcessResult] | None = None,
        single_scenario_list: List[SingleScenario] | None = None,
        broker_scenario_map: Dict[BrokerType, BrokerScenarioInfo] | None = None
    ):
        self._batch_execution_time = batch_execution_time
        self._batch_warmup_time = batch_warmup_time
        self._batch_tickrun_time = batch_tickrun_time
        self._process_result_list = process_result_list or []
        self._single_scenario_list = single_scenario_list or []
        self._broker_scenario_map = broker_scenario_map or {}

    @property
    def batch_execution_time(self) -> float:
        return self._batch_execution_time

    @property
    def batch_warmup_time(self) -> float:
        return self._batch_warmup_time

    @property
    def batch_tickrun_time(self) -> float:
        return self._batch_tickrun_time

    @property
    def process_result_list(self) -> List[ProcessResult]:
        return self._process_result_list

    @property
    def single_scenario_list(self) -> List[SingleScenario]:
        return self._single_scenario_list

    @property
    def broker_scenario_map(self) -> Dict[BrokerType, BrokerScenarioInfo]:
        return self._broker_scenario_map

    def get_scenario_by_process_result(self, process_result: ProcessResult) -> SingleScenario:
        """Return the scenario belonging to a given process result."""
        return self._single_scenario_list[process_result.scenario_index]
