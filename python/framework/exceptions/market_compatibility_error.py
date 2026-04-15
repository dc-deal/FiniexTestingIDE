"""
FiniexTestingIDE - Market Compatibility Error
Raised when a worker's required activity metric is incompatible with the
scenario's broker market.
"""

from typing import Optional


class MarketCompatibilityError(ValueError):
    """
    Raised when a worker declares an activity metric that the scenario's
    broker market does not provide.

    Subclasses ValueError so the existing ValidationResult reporting handles
    it as a regular pre-flight validation error — the failing scenario is
    marked, the batch continues with remaining valid scenarios.
    """

    def __init__(
        self,
        scenario_name: str,
        worker_instance_name: str,
        worker_type: str,
        required_metric: str,
        broker_type: str,
        broker_metric: Optional[str],
        market_type: str,
    ):
        """
        Build a structured, user-actionable error message.

        Args:
            scenario_name: Name of the failing scenario
            worker_instance_name: User-defined instance name (e.g. 'obv_volume')
            worker_type: Worker reference (e.g. 'CORE/obv')
            required_metric: Activity metric required by the worker
            broker_type: Broker type identifier (e.g. 'mt5', 'kraken_spot')
            broker_metric: Primary activity metric provided by the broker market
            market_type: Market type of the broker (e.g. 'forex', 'crypto')
        """
        self.scenario_name = scenario_name
        self.worker_instance_name = worker_instance_name
        self.worker_type = worker_type
        self.required_metric = required_metric
        self.broker_type = broker_type
        self.broker_metric = broker_metric
        self.market_type = market_type

        message = (
            f"Worker '{worker_instance_name}' ({worker_type}) requires "
            f"activity metric '{required_metric}', but broker '{broker_type}' "
            f"provides '{broker_metric}' (market: {market_type}). "
            f"Remove this worker from the scenario, or switch to a broker "
            f"whose market provides '{required_metric}'."
        )
        super().__init__(message)
