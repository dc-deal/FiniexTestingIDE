"""
Balanced Strategy Generator
============================
Generates scenarios with equal distribution across volatility regimes.

Location: python/scenario/generator/balanced_generator.py
"""

from typing import Dict, List, Optional

from python.framework.types.scenario_generator_types import (
    GeneratorConfig,
    PeriodAnalysis,
    ScenarioCandidate,
    VolatilityRegime,
)
from python.components.logger.bootstrap_logger import get_logger

vLog = get_logger()


class BalancedGenerator:
    """
    Balanced scenario generator.

    Selects equal number of scenarios from each volatility regime
    for comprehensive strategy testing across market conditions.
    """

    def __init__(self, config: GeneratorConfig):
        """
        Initialize balanced generator.

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
        Generate balanced scenarios across volatility regimes.

        Args:
            symbol: Trading symbol
            periods: Available periods
            count: Total scenarios to generate
            max_ticks: Max ticks per scenario

        Returns:
            List of scenario candidates
        """
        # Group periods by regime
        regime_periods: Dict[VolatilityRegime, List[PeriodAnalysis]] = {
            regime: [] for regime in VolatilityRegime
        }

        for period in periods:
            regime_periods[period.regime].append(period)

        # Calculate scenarios per regime
        regime_count = self._config.balanced.regime_count
        per_regime = max(1, count // regime_count)

        scenarios = []

        for regime in VolatilityRegime:
            regime_list = regime_periods[regime]

            if not regime_list:
                vLog.debug(f"No periods for regime {regime.value}")
                continue

            # Sort by quality (real bar ratio)
            regime_list = sorted(
                regime_list,
                key=lambda p: (
                    p.real_bar_count / max(p.bar_count, 1),
                    p.tick_count
                ),
                reverse=True
            )

            # Select top periods
            selected = regime_list[:per_regime]

            for period in selected:
                candidate = self._period_to_candidate(
                    symbol, period, max_ticks
                )
                scenarios.append(candidate)

        # If we need more scenarios, fill from best remaining
        if len(scenarios) < count:
            used_times = {s.start_time for s in scenarios}
            remaining = [
                p for p in periods
                if p.start_time not in used_times
            ]

            remaining = sorted(
                remaining,
                key=lambda p: p.tick_count,
                reverse=True
            )

            for period in remaining:
                if len(scenarios) >= count:
                    break

                candidate = self._period_to_candidate(
                    symbol, period, max_ticks
                )
                scenarios.append(candidate)

        return scenarios[:count]

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
