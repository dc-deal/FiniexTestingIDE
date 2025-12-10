"""
Stress Strategy Generator
==========================
Generates scenarios focusing on high volatility and activity periods.

Location: python/scenario/generator/stress_generator.py
"""

from typing import List, Optional

from python.framework.types.scenario_generator_types import (
    GeneratorConfig,
    PeriodAnalysis,
    ScenarioCandidate,
)
from python.components.logger.bootstrap_logger import get_logger

vLog = get_logger()


class StressGenerator:
    """
    Stress scenario generator.

    Selects periods with extreme market conditions to test
    strategy robustness under high volatility and activity.
    """

    def __init__(self, config: GeneratorConfig):
        """
        Initialize stress generator.

        Args:
            config: Generator configuration
        """
        self._config = config

    def generate(
        self,
        symbol: str,
        periods: List[PeriodAnalysis],
        count: int,
        max_ticks: Optional[int]
    ) -> List[ScenarioCandidate]:
        """
        Generate stress scenarios with extreme conditions.

        Selects top percentile periods by:
        - Tick density (high activity)
        - ATR (high volatility)

        Args:
            symbol: Trading symbol
            periods: Available periods
            count: Number of scenarios to generate
            max_ticks: Max ticks per scenario

        Returns:
            List of scenario candidates
        """
        if not periods:
            return []

        # Calculate percentile thresholds
        sorted_by_density = sorted(periods, key=lambda p: p.tick_density)
        sorted_by_atr = sorted(periods, key=lambda p: p.atr)

        density_threshold = sorted_by_density[
            int(len(sorted_by_density) * self._config.stress.volatility_percentile)
        ].tick_density

        atr_threshold = sorted_by_atr[
            int(len(sorted_by_atr) * self._config.stress.activity_percentile)
        ].atr

        vLog.debug(
            f"Stress thresholds: density > {density_threshold:.2f}, "
            f"ATR > {atr_threshold:.4f}"
        )

        # Filter periods meeting stress criteria
        stress_periods = [
            p for p in periods
            if p.tick_density >= density_threshold or p.atr >= atr_threshold
        ]

        if not stress_periods:
            vLog.warning("No periods meet stress criteria, using top periods")
            stress_periods = periods

        # Sort by combined score (density + normalized ATR)
        max_atr = max(p.atr for p in stress_periods)
        stress_periods = sorted(
            stress_periods,
            key=lambda p: p.tick_density +
            (p.atr / max_atr if max_atr > 0 else 0),
            reverse=True
        )

        # Select top N periods
        selected = stress_periods[:count]

        scenarios = []
        for period in selected:
            candidate = self._period_to_candidate(symbol, period, max_ticks)
            scenarios.append(candidate)

        return scenarios

    def _period_to_candidate(
        self,
        symbol: str,
        period: PeriodAnalysis,
        max_ticks: Optional[int]
    ) -> ScenarioCandidate:
        """
        Convert period analysis to scenario candidate.

        Args:
            symbol: Trading symbol
            period: Period analysis
            max_ticks: Max ticks override

        Returns:
            ScenarioCandidate
        """
        estimated = period.tick_count
        if max_ticks:
            estimated = min(estimated, max_ticks)

        real_ratio = period.real_bar_count / max(period.bar_count, 1)

        return ScenarioCandidate(
            symbol=symbol,
            start_time=period.start_time,
            end_time=period.end_time,
            regime=period.regime,
            session=period.session,
            estimated_ticks=estimated,
            atr=period.atr,
            tick_density=period.tick_density,
            real_bar_ratio=real_ratio
        )
