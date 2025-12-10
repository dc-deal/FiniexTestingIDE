"""
Scenario Generator - Main Orchestrator
======================================
Coordinates analysis and dispatches to strategy-specific generators.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from python.framework.reporting.market_analyzer_report import MarketAnalyzer
from python.framework.types.scenario_generator_types import (
    GenerationResult,
    GenerationStrategy,
    GeneratorConfig,
    PeriodAnalysis,
    ScenarioCandidate,
    TradingSession,
    VolatilityRegime,
)
from python.components.logger.bootstrap_logger import get_logger
from python.scenario.generator.balanced_generator import BalancedGenerator
from python.scenario.generator.blocks_generator import BlocksGenerator
from python.scenario.generator.stress_generator import StressGenerator

vLog = get_logger()


class ScenarioGenerator:
    """
    Main scenario generation orchestrator.

    Coordinates market analysis and dispatches to strategy-specific generators.
    """

    def __init__(
        self,
        data_dir: str = "./data/processed",
        config_path: Optional[str] = None
    ):
        """
        Initialize scenario generator.

        Args:
            data_dir: Path to processed data directory
            config_path: Path to generator config JSON
        """
        self._data_dir = Path(data_dir)
        self._analyzer = MarketAnalyzer(str(self._data_dir), config_path)
        self._config = self._analyzer.get_config()

        # Template paths
        self._template_path = Path(
            "./configs/generator/template_scenario_set_header.json")
        self._output_dir = Path("./configs/scenario_sets")
        self._output_dir.mkdir(parents=True, exist_ok=True)

        # Initialize strategy generators
        self._balanced_gen = BalancedGenerator(self._config)
        self._blocks_gen = BlocksGenerator(self._data_dir, self._config)
        self._stress_gen = StressGenerator(self._config)

    # =========================================================================
    # MAIN GENERATION
    # =========================================================================

    def generate(
        self,
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
            symbols: List of symbols to generate for
            strategy: Generation strategy
            count: Number of scenarios
            block_hours: Block size for blocks strategy
            session_filter: Filter by session name
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

        # Analyze the symbol
        analysis = self._analyzer.analyze_symbol(symbol)

        # Filter periods (for balanced/stress strategies)
        periods = self._filter_periods(
            analysis.periods,
            session_filter,
            start_filter,
            end_filter
        )

        if not periods and strategy != GenerationStrategy.BLOCKS:
            raise ValueError(
                f"No periods match the given filters for {symbol}")

        vLog.info(f"Generating {count} {strategy.value} scenarios from "
                  f"{len(periods)} periods")

        # Dispatch to strategy-specific generator
        if strategy == GenerationStrategy.BALANCED:
            effective_count = count or 10
            vLog.info(f"Generating {effective_count} {strategy.value} scenarios from "
                      f"{len(periods)} periods")
            scenarios = self._balanced_gen.generate(
                symbol, periods, effective_count, max_ticks
            )

        elif strategy == GenerationStrategy.BLOCKS:
            hours = block_hours or self._config.blocks.default_block_hours
            scenarios = self._blocks_gen.generate(
                symbol, hours, count, sessions_filter
            )
            session_info = f", sessions: {sessions_filter}" if sessions_filter else ""
            vLog.info(
                f"Generated {len(scenarios)} blocks (max {hours}h each{session_info})")

        elif strategy == GenerationStrategy.STRESS:
            effective_count = count or 5
            vLog.info(f"Generating {effective_count} {strategy.value} scenarios from "
                      f"{len(periods)} periods")
            scenarios = self._stress_gen.generate(
                symbol, periods, effective_count, max_ticks
            )

        else:
            raise ValueError(f"Unknown strategy: {strategy}")

        return self._build_result(symbol, strategy, scenarios)

    # =========================================================================
    # SHARED HELPER METHODS
    # =========================================================================

    def _filter_periods(
        self,
        periods: List[PeriodAnalysis],
        session_filter: Optional[str],
        start_filter: Optional[datetime],
        end_filter: Optional[datetime]
    ) -> List[PeriodAnalysis]:
        """
        Filter periods by session and time range.

        Args:
            periods: All periods
            session_filter: Session name filter
            start_filter: Start datetime
            end_filter: End datetime

        Returns:
            Filtered periods
        """
        filtered = periods

        # Session filter
        if session_filter:
            session = TradingSession(session_filter)
            filtered = [p for p in filtered if p.session == session]

        # Time filters
        if start_filter:
            filtered = [p for p in filtered if p.start_time >= start_filter]

        if end_filter:
            filtered = [p for p in filtered if p.end_time <= end_filter]

        # Quality filter: prefer real bars
        if self._config.balanced.prefer_real_bars:
            filtered = sorted(
                filtered,
                key=lambda p: p.real_bar_count / max(p.bar_count, 1),
                reverse=True
            )

        return filtered

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

    # =========================================================================
    # CONFIG SAVING
    # =========================================================================

    def save_config(
        self,
        result: GenerationResult,
        filename: str
    ) -> Path:
        """
        Save generation result as scenario set config.

        Args:
            result: Generation result
            filename: Output filename

        Returns:
            Path to saved config file
        """
        # Load template
        if not self._template_path.exists():
            raise FileNotFoundError(
                f"Scenario template not found: {self._template_path}\n"
                f"Expected location: ./configs/generator/template_scenario_set_header.json\n"
                f"This file is required for generating scenario configs."
            )

        with open(self._template_path, 'r') as f:
            config = json.load(f)

        # Update metadata
        config['version'] = "1.0"
        config['scenario_set_name'] = filename.replace('.json', '')
        config['created'] = datetime.now(timezone.utc).isoformat()

        # Add scenarios
        scenarios = result.scenarios
        config['scenarios'] = []
        for i, candidate in enumerate(scenarios, 1):
            name = f"{result.symbol}_{result.strategy.value}_{i:02d}"
            # Blocks strategy: max_ticks = None (time-based only)
            use_max_ticks = None if result.strategy == GenerationStrategy.BLOCKS else candidate.estimated_ticks
            scenario_dict = candidate.to_scenario_dict(name, use_max_ticks)
            config['scenarios'].append(scenario_dict)

        # Save to file
        output_path = self._output_dir / filename
        with open(output_path, 'w') as f:
            json.dump(config, f, indent=2, default=str)

        vLog.info(f"Saved {len(scenarios)} scenarios to {output_path}")

        return output_path
