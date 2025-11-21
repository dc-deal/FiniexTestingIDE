"""
FiniexTestingIDE - Scenario Requirements Calculator
Calculates warmup and timeframe requirements from workers

"""

from typing import List
from python.framework.types.scenario_types import ScenarioRequirements


def calculate_scenario_requirements(workers: List) -> ScenarioRequirements:
    """
    Calculate requirements for a scenario based on its workers.

    Each scenario calculates its own requirements independently, allowing
    different scenarios to use completely different worker configurations.

    Process:
    1. Collect warmup requirements from each worker
    2. Collect required timeframes from each worker
    3. Calculate maximum warmup per timeframe (workers may overlap)
    4. Determine overall maximum warmup bars needed

    Args:
        workers: List of worker instances for this scenario

    Returns:
        ScenarioRequirements with max_warmup_bars, all_timeframes,
        warmup_by_timeframe, and total_workers

    Example:
        >>> workers = [rsi_worker, macd_worker, ema_worker]
        >>> reqs = calculate_scenario_requirements(workers)
        >>> print(reqs.max_warmup_bars)  # 200
        >>> print(reqs.all_timeframes)   # ['M5', 'M30']
        >>> print(reqs.warmup_by_timeframe)  # {'M5': 14, 'M30': 20}
    """
    # Collect warmup requirements and timeframes directly from workers
    all_warmup_reqs = []
    all_timeframes = set()
    warmup_by_tf = {}

    for worker in workers:
        # Get warmup requirements from worker instance
        warmup_reqs = worker.get_warmup_requirements()
        all_warmup_reqs.append(warmup_reqs)

        # Get required timeframes from worker instance
        timeframes = worker.get_required_timeframes()
        all_timeframes.update(timeframes)

        # Track max warmup per timeframe
        for tf, bars in warmup_reqs.items():
            warmup_by_tf[tf] = max(warmup_by_tf.get(tf, 0), bars)

    # Calculate maximum warmup bars needed for this scenario
    max_warmup = max(
        [max(reqs.values()) for reqs in all_warmup_reqs if reqs],
        default=50
    )

    return ScenarioRequirements(
        max_warmup_bars=max_warmup,
        all_timeframes=list(all_timeframes),
        warmup_by_timeframe=warmup_by_tf,
        total_workers=len(workers)
    )
