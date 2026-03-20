"""
Scenario Generator - Main Orchestrator
======================================
Coordinates analysis and dispatches to strategy-specific generators.
"""

from datetime import datetime, timezone
from typing import List, Optional

from python.configuration.generator_config_loader import GeneratorConfigLoader
from python.framework.discoveries.volatility_profile_analyzer.volatility_profile_analyzer import VolatilityProfileAnalyzer
from python.framework.types.market_types.market_config_types import MarketType
from python.framework.types.market_types.market_volatility_profile_types import (
    TradingSession,
    VolatilityRegime,
)
from python.framework.types.scenario_types.scenario_generator_types import (
    GenerationResult,
    GenerationStrategy,
    ScenarioCandidate,
)
from python.framework.logging.bootstrap_logger import get_global_logger

from .blocks_generator import BlocksGenerator

vLog = get_global_logger()


class ScenarioGenerator:
    """
    Main scenario generation orchestrator.

    Coordinates market analysis and dispatches to strategy-specific generators.
    """

    def __init__(self):
        """Initialize scenario generator with volatility profile analyzer and strategy generators."""
        self._analyzer = VolatilityProfileAnalyzer()
        self._config = GeneratorConfigLoader().get_generator_config()

        # Initialize strategy generator
        self._blocks_gen = BlocksGenerator(self._config)

    # =========================================================================
    # MAIN GENERATION
    # =========================================================================

    def generate(
        self,
        broker_type: str,
        symbols: List[str],
        strategy: GenerationStrategy,
        count: Optional[int] = None,
        block_hours: Optional[int] = None,
        session_filter: Optional[str] = None,
        sessions_filter: Optional[List[str]] = None,
        start_filter: Optional[datetime] = None,
        end_filter: Optional[datetime] = None,
        max_ticks: Optional[int] = None
    ) -> GenerationResult:
        """
        Generate scenario candidates.

        Args:
            broker_type: Broker type identifier (e.g., 'mt5', 'kraken_spot')
            symbols: List of symbols to generate for
            strategy: Generation strategy
            count: Number of scenarios
            block_hours: Block size in hours
            session_filter: Filter by session name (deprecated)
            sessions_filter: Filter by multiple session names
            start_filter: Start date filter
            end_filter: End date filter
            max_ticks: Max ticks per scenario

        Returns:
            GenerationResult with selected scenarios
        """
        # For now, support single symbol generation
        if len(symbols) > 1:
            vLog.warning(
                "Multi-symbol generation not yet implemented. Using first symbol.")

        symbol = symbols[0]

        # Build volatility profile (for metadata)
        profile = self._analyzer.build_profile(broker_type, symbol)

        # Warn: session filter on non-forex markets (no real sessions, time-of-day only)
        if sessions_filter and profile.market_type != MarketType.FOREX:
            vLog.warning(
                f"⚠️ Session filter {sessions_filter} used with "
                f"{profile.market_type.value} market. "
                f"{profile.market_type.value.capitalize()} has no defined trading sessions — "
                f"filter acts as time-of-day separation only."
            )

        # Note: Blocks generator handles its own data access
        vLog.info(f"Generating scenarios using {strategy.value} strategy")

        hours = block_hours or self._config.blocks.default_block_hours
        scenarios = self._blocks_gen.generate(
            broker_type, symbol, hours, count, sessions_filter
        )
        session_info = f", sessions: {sessions_filter}" if sessions_filter else ""
        vLog.info(
            f"Generated {len(scenarios)} blocks (max {hours}h each{session_info})")

        return self._build_result(symbol, strategy, scenarios)

    # =========================================================================
    # RESULT BUILDING
    # =========================================================================

    def _build_result(
        self,
        symbol: str,
        strategy: GenerationStrategy,
        scenarios: List[ScenarioCandidate]
    ) -> GenerationResult:
        """
        Build generation result from scenarios.

        Args:
            symbol: Trading symbol
            strategy: Strategy used
            scenarios: Generated scenarios

        Returns:
            GenerationResult
        """
        total_ticks = sum(s.estimated_ticks for s in scenarios)
        avg_ticks = total_ticks / len(scenarios) if scenarios else 0

        # Regime coverage
        regime_coverage = {regime: 0 for regime in VolatilityRegime}
        for s in scenarios:
            regime_coverage[s.regime] += 1

        # Session coverage
        session_coverage = {session: 0 for session in TradingSession}
        for s in scenarios:
            session_coverage[s.session] += 1

        return GenerationResult(
            symbol=symbol,
            strategy=strategy,
            scenarios=scenarios,
            total_estimated_ticks=total_ticks,
            avg_ticks_per_scenario=avg_ticks,
            regime_coverage=regime_coverage,
            session_coverage=session_coverage,
            generated_at=datetime.now(timezone.utc),
            config_used=self._config
        )

