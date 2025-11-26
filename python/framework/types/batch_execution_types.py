from dataclasses import dataclass
from typing import Dict, List

from python.framework.trading_env.broker_config import BrokerType
from python.framework.types.process_data_types import PostProcessResult, ProcessResult
from python.framework.types.scenario_set_types import BrokerScenarioInfo, SingleScenario


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
    scenario_list:  List[PostProcessResult] = None
    broker_scenario_map: Dict[BrokerType, BrokerScenarioInfo] = None

    @classmethod
    def from_process_results(
        cls,
        scenarios: List[SingleScenario],
        results: List[ProcessResult],
        summary_execution_time: float,
        broker_scenario_map: Dict[BrokerType, BrokerScenarioInfo]
    ) -> "BatchExecutionSummary":
        """ 
            create post results to attach singleSenario object.
        """
        post_results = [
            PostProcessResult.from_process_result(
                process_result=r,
                scenario=scenarios[r.scenario_index]
            )
            for r in results
        ]

        return cls(
            success=True,
            scenarios_count=len(scenarios),
            summary_execution_time=summary_execution_time,
            broker_scenario_map=broker_scenario_map,
            scenario_list=post_results,
        )
