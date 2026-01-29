"""
Scenario Generator - Main Orchestrator
======================================
Coordinates analysis and dispatches to strategy-specific generators.

Location: python/scenario/generator/scenario_generator.py
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
    ScenarioCandidate,
    TradingSession,
    VolatilityRegime,
)
from python.framework.logging.bootstrap_logger import get_global_logger

from .blocks_generator import BlocksGenerator
from .stress_generator import StressGenerator

vLog = get_global_logger()


class ScenarioGenerator:
    """
    Main scenario generation orchestrator.

    Coordinates market analysis and dispatches to strategy-specific generators.
    """

    def __init__(
        self,
        data_dir: str = "./data/processed"
    ):
        """
        Initialize scenario generator.

        Args:
            data_dir: Path to processed data directory
            config_path: Path to generator config JSON
        """
        self._data_dir = Path(data_dir)
        self._analyzer = MarketAnalyzer(str(self._data_dir))
        self._config = self._analyzer.get_config()

        # Template paths
        self._template_path = Path(
            "./configs/generator/template_scenario_set_header.json")
        self._output_dir = Path("./configs/scenario_sets")
        self._output_dir.mkdir(parents=True, exist_ok=True)

        # Initialize strategy generators
        self._blocks_gen = BlocksGenerator(self._data_dir, self._config)
        self._stress_gen = StressGenerator(self._config, self._analyzer)

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
            block_hours: Block size for blocks/stress strategy
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

        # Analyze the symbol (for metadata)
        analysis = self._analyzer.analyze_symbol(broker_type, symbol)

        # Note: Blocks and Stress generators handle their own data access
        vLog.info(f"Generating scenarios using {strategy.value} strategy")

        # Dispatch to strategy-specific generator
        if strategy == GenerationStrategy.BLOCKS:
            hours = block_hours or self._config.blocks.default_block_hours
            scenarios = self._blocks_gen.generate(
                broker_type, symbol, hours, count, sessions_filter
            )
            session_info = f", sessions: {sessions_filter}" if sessions_filter else ""
            vLog.info(
                f"Generated {len(scenarios)} blocks (max {hours}h each{session_info})")

        elif strategy == GenerationStrategy.STRESS:
            hours = block_hours or self._config.stress.stress_scenario_hours
            effective_count = count or 5
            vLog.info(
                f"Generating {effective_count} {strategy.value} scenarios")
            scenarios = self._stress_gen.generate(
                broker_type, symbol, hours, effective_count, max_ticks
            )

        else:
            raise ValueError(f"Unknown strategy: {strategy}")

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
            # Blocks/Stress strategy: max_ticks = None (time-based only)
            use_max_ticks = None if result.strategy in [
                GenerationStrategy.BLOCKS,
                GenerationStrategy.STRESS
            ] else candidate.estimated_ticks
            scenario_dict = candidate.to_scenario_dict(name, use_max_ticks)
            config['scenarios'].append(scenario_dict)

        # Save to file
        output_path = self._output_dir / filename
        with open(output_path, 'w') as f:
            json.dump(config, f, indent=2, default=str)

        vLog.info(f"Saved {len(scenarios)} scenarios to {output_path}")

        return output_path
