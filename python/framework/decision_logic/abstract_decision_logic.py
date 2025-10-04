"""
FiniexTestingIDE - Abstract Decision Logic
Base class for all decision logic implementations

Decision Logic orchestrates worker results into trading decisions.
This layer is separate from worker coordination - it focuses purely
on decision-making strategy, not on worker management.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List

from python.framework.types import Bar, Decision, TickData, WorkerResult


class AbstractDecisionLogic(ABC):
    """
    Abstract base class for decision logic implementations.

    Decision Logic takes worker results and generates trading decisions.
    It does NOT manage workers - that's WorkerCoordinator's job.

    Philosophy (from Worker Manifest):
    - Workers are atomic units (first level)
    - DecisionLogic orchestrates results (second level)
    - No sub-workers, no hidden dependencies

    Example:
        class SimpleConsensus(AbstractDecisionLogic):
            def get_required_workers(self):
                return ["rsi", "envelope"]

            def compute(self, tick, worker_results, bars, history):
                rsi = worker_results["rsi"].value
                envelope = worker_results["envelope"].value

                if rsi < 30 and envelope["position"] < 0.3:
                    return Decision(action="BUY", confidence=0.8)

                return Decision(action="FLAT", confidence=0.5)
    """

    def __init__(self, name: str, config: Dict[str, Any] = None):
        """
        Initialize decision logic.

        Args:
            name: Logic identifier (e.g., "simple_consensus")
            config: Logic-specific configuration
        """
        self.name = name
        self.config = config or {}
        self._statistics = {
            "decisions_made": 0,
            "buy_signals": 0,
            "sell_signals": 0,
            "flat_signals": 0,
        }

    @abstractmethod
    def get_required_workers(self) -> List[str]:
        """
        Declare which workers this logic needs.

        Factory uses this to instantiate the correct workers.
        Worker names must match the worker_types in scenario config.

        Returns:
            List of worker names (e.g., ["rsi", "envelope", "macd"])
        """
        pass

    @abstractmethod
    def compute(
        self,
        tick: TickData,
        worker_results: Dict[str, WorkerResult],
        current_bars: Dict[str, Bar],
        bar_history: Dict[str, List[Bar]],
    ) -> Decision:
        """
        Generate trading decision based on worker results.

        This is the core decision-making method. It receives all worker
        outputs and must return a structured Decision object.

        Args:
            tick: Current tick data
            worker_results: Dict[worker_name, WorkerResult] - All worker outputs
            current_bars: Current bars per timeframe
            bar_history: Historical bars per timeframe

        Returns:
            Decision object with action/confidence/reason
        """
        pass

    def get_statistics(self) -> Dict[str, Any]:
        """
        Get decision logic statistics.

        Returns:
            Dict with decision counts and performance metrics
        """
        return self._statistics.copy()

    def _update_statistics(self, decision: Decision):
        """
        Update internal statistics after decision.

        Called automatically after compute() by orchestrator.
        """
        self._statistics["decisions_made"] += 1

        if decision.action == "BUY":
            self._statistics["buy_signals"] += 1
        elif decision.action == "SELL":
            self._statistics["sell_signals"] += 1
        elif decision.action == "FLAT":
            self._statistics["flat_signals"] += 1

    def validate_worker_results(self, worker_results: Dict[str, WorkerResult]) -> bool:
        """
        Validate that all required workers provided results.

        Called by orchestrator before compute().
        Override for custom validation logic.

        Args:
            worker_results: Dict of worker outputs

        Returns:
            True if valid, raises ValueError if invalid
        """
        required = self.get_required_workers()
        missing = [w for w in required if w not in worker_results]

        if missing:
            raise ValueError(
                f"DecisionLogic '{self.name}': Missing worker results: {missing}"
            )

        return True

    def get_config_value(self, key: str, default: Any = None) -> Any:
        """
        Get configuration value with default fallback.

        Helper method for accessing logic-specific config.

        Args:
            key: Config key
            default: Default value if key not found

        Returns:
            Config value or default
        """
        return self.config.get(key, default)
