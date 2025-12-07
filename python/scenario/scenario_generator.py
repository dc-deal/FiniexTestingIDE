"""
Scenario Generator
==================
Generates test scenario configurations based on market analysis.

Strategies:
- balanced: Equal distribution across volatility regimes
- blocks: Chronological time blocks with tick balancing
- stress: High volatility and high activity periods

Location: python/scenario/generator.py
"""

import json
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np

from python.data_worker.data_loader.tick_index_manager import TickIndexManager
from python.framework.reporting.coverage_report import CoverageReport
from python.framework.utils.market_calendar import GapCategory
from python.framework.utils.market_session_utils import get_session_from_utc_hour
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

vLog = get_logger()


class ScenarioGenerator:
    """
    Generates scenario configurations from market analysis.

    Uses MarketAnalyzer results to select optimal time periods
    based on volatility, activity, and data quality.
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
        # NEW: Multiple sessions for blocks
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
            start_filter: Start date filter
            end_filter: End date filter
            max_ticks: Max ticks per scenario

        Returns:
            GenerationResult with selected scenarios
        """
        # For now, support single symbol generation
        # Multi-symbol can be added later
        if len(symbols) > 1:
            vLog.warning(
                "Multi-symbol generation not yet implemented. Using first symbol.")

        symbol = symbols[0]

        # Analyze the symbol
        analysis = self._analyzer.analyze_symbol(symbol)

        # Filter periods
        periods = self._filter_periods(
            analysis.periods,
            session_filter,
            start_filter,
            end_filter
        )

        if not periods:
            raise ValueError(
                f"No periods match the given filters for {symbol}")

        vLog.info(f"Generating {count} {strategy.value} scenarios from "
                  f"{len(periods)} periods")

        # Generate based on strategy
        if strategy == GenerationStrategy.BALANCED:
            effective_count = count or 10
            vLog.info(f"Generating {effective_count} {strategy.value} scenarios from "
                      f"{len(periods)} periods")
            scenarios = self._generate_balanced(
                symbol, periods, effective_count, max_ticks
            )
        elif strategy == GenerationStrategy.BLOCKS:
            hours = block_hours or self._config.blocks.default_block_hours
            # Blocks strategy uses coverage report for gap-aware generation
            scenarios = self._generate_blocks_from_coverage(
                symbol, hours, count, sessions_filter
            )
            session_info = f", sessions: {sessions_filter}" if sessions_filter else ""
            vLog.info(
                f"Generated {len(scenarios)} blocks (max {hours}h each{session_info})")

        elif strategy == GenerationStrategy.STRESS:
            effective_count = count or 5
            vLog.info(f"Generating {effective_count} {strategy.value} scenarios from "
                      f"{len(periods)} periods")
            scenarios = self._generate_stress(
                symbol, periods, effective_count, max_ticks
            )
        else:
            raise ValueError(f"Unknown strategy: {strategy}")
        # Build result
        return self._build_result(symbol, strategy, scenarios)

    # =========================================================================
    # FILTERING
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
            # Sort by real bar ratio descending
            filtered = sorted(
                filtered,
                key=lambda p: p.real_bar_count / max(p.bar_count, 1),
                reverse=True
            )

        return filtered

    # =========================================================================
    # BALANCED STRATEGY
    # =========================================================================

    def _generate_balanced(
        self,
        symbol: str,
        periods: List[PeriodAnalysis],
        count: int,
        max_ticks: Optional[int]
    ) -> List[ScenarioCandidate]:
        """
        Generate balanced scenarios across volatility regimes.

        Selects equal number from each regime for comprehensive testing.

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

            # Sort remaining by tick count
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

    # =========================================================================
    # BLOCKS STRATEGY
    # =========================================================================

    def _generate_blocks_from_coverage(
        self,
        symbol: str,
        block_hours: int,
        count_max: Optional[int],
        sessions_filter: Optional[List[str]] = None
    ) -> List[ScenarioCandidate]:
        """
        Generate chronological blocks based on coverage report.

        Uses gap analysis to create blocks only within continuous data regions.
        Gaps of type moderate/large/weekend interrupt blocks.

        Args:
            symbol: Trading symbol
            block_hours: Maximum hours per block
            count_max: Optional limit on number of blocks (None = all)
            sessions_filter: Optional list of session names to include

        Returns:
            List of scenario candidates with max_ticks=None
        """
        # Get tick index for coverage report
        tick_index = TickIndexManager(self._data_dir)
        tick_index.build_index()

        # Generate coverage report
        coverage_report = tick_index.get_coverage_report(symbol)

        if not coverage_report.files:
            raise ValueError(f"No tick data found for {symbol}")

        # Extract continuous regions (between interrupting gaps)
        continuous_regions = self._extract_continuous_regions(coverage_report)

        if not continuous_regions:
            raise ValueError(f"No continuous data regions found for {symbol}")

        # Convert sessions_filter to TradingSession enums if provided
        allowed_sessions = None
        if sessions_filter:
            allowed_sessions = set()
            for s in sessions_filter:
                try:
                    allowed_sessions.add(TradingSession(s))
                except ValueError:
                    vLog.warning(f"Unknown session '{s}', ignoring")

            if allowed_sessions:
                vLog.info(
                    f"Filtering blocks to sessions: {[s.value for s in allowed_sessions]}")

        # Log gap filtering info
        filtered_gaps = [
            g for g in coverage_report.gaps
            if g.category in [GapCategory.MODERATE, GapCategory.LARGE, GapCategory.WEEKEND]
        ]

        total_coverage_hours = sum(
            (r['end'] - r['start']).total_seconds() / 3600
            for r in continuous_regions
        )
        total_gap_hours = sum(g.gap_hours for g in filtered_gaps)

        vLog.info(
            f"Coverage: {total_coverage_hours:.1f}h usable, "
            f"{total_gap_hours:.1f}h gaps filtered "
            f"({len(filtered_gaps)} gaps: "
            f"{coverage_report.gap_counts['weekend']} weekend, "
            f"{coverage_report.gap_counts['moderate']} moderate, "
            f"{coverage_report.gap_counts['large']} large)"
        )

        # Generate blocks from continuous regions
        scenarios = []
        merge_threshold = timedelta(
            minutes=self._config.blocks.merge_remainder_threshold_minutes
        )
        block_duration = timedelta(hours=block_hours)

        for region in continuous_regions:
            region_start = region['start']
            region_end = region['end']
            region_duration = region_end - region_start

            # Skip regions smaller than merge threshold
            if region_duration < merge_threshold:
                continue

            # If session filter active, extract only hours within allowed sessions
            if allowed_sessions:
                session_windows = self._extract_session_windows(
                    region_start, region_end, allowed_sessions
                )
            else:
                # No filter - entire region is one window
                session_windows = [{'start': region_start, 'end': region_end}]

            # Generate blocks from session windows
            for window in session_windows:
                window_start = window['start']
                window_end = window['end']
                window_duration = window_end - window_start

                if window_duration < merge_threshold:
                    continue

                current_start = window_start
                window_blocks = []

                while current_start < window_end:
                    remaining = window_end - current_start

                    if remaining <= block_duration:
                        # Last block in window
                        if remaining < merge_threshold and window_blocks:
                            # Merge with previous block
                            prev_block = window_blocks[-1]
                            prev_block['end'] = window_end
                        else:
                            # Create final block
                            window_blocks.append({
                                'start': current_start,
                                'end': window_end
                            })
                        break
                    else:
                        # Full block
                        block_end = current_start + block_duration
                        window_blocks.append({
                            'start': current_start,
                            'end': block_end
                        })
                        current_start = block_end

                # Convert window blocks to candidates
                for block in window_blocks:
                    candidate = ScenarioCandidate(
                        symbol=symbol,
                        start_time=block['start'],
                        end_time=block['end'],
                        regime=VolatilityRegime.MEDIUM,
                        session=TradingSession.LONDON,
                        estimated_ticks=0,
                        atr=0.0,
                        tick_density=0.0,
                        real_bar_ratio=1.0
                    )
                    scenarios.append(candidate)

        # Apply count_max limit if specified
        if count_max and len(scenarios) > count_max:
            scenarios = scenarios[:count_max]
            vLog.info(f"Limited to {count_max} blocks (from {len(scenarios)})")

        return scenarios

    def _extract_continuous_regions(
        self,
        coverage_report: CoverageReport
    ) -> List[Dict[str, datetime]]:
        """
        Extract continuous data regions from coverage report.

        Regions are split by moderate, large, and weekend gaps.

        Args:
            coverage_report: Analyzed coverage report

        Returns:
            List of dicts with 'start' and 'end' datetime keys
        """
        if not coverage_report.files:
            return []

        # Gap categories that interrupt blocks
        interrupting_categories = {
            GapCategory.MODERATE,
            GapCategory.LARGE,
            GapCategory.WEEKEND
        }

        regions = []
        current_start = coverage_report.files[0].start_time

        for gap in coverage_report.gaps:
            if gap.category in interrupting_categories:
                # End current region at gap start
                region_end = gap.file1.end_time

                if region_end > current_start:
                    regions.append({
                        'start': current_start,
                        'end': region_end
                    })

                # Start new region after gap
                current_start = gap.file2.start_time

        # Add final region
        final_end = coverage_report.files[-1].end_time
        if final_end > current_start:
            regions.append({
                'start': current_start,
                'end': final_end
            })

        return regions

    def _extract_session_windows(
        self,
        start: datetime,
        end: datetime,
        allowed_sessions: set
    ) -> List[Dict[str, datetime]]:
        """
        Extract time windows within allowed trading sessions.

        Iterates hour by hour and groups consecutive hours in allowed sessions.

        Args:
            start: Region start time
            end: Region end time
            allowed_sessions: Set of TradingSession enums to include

        Returns:
            List of dicts with 'start' and 'end' datetime keys
        """
        windows = []
        current_window_start = None
        current_hour = start.replace(minute=0, second=0, microsecond=0)

        while current_hour < end:
            hour_session = get_session_from_utc_hour(current_hour.hour)

            if hour_session in allowed_sessions:
                if current_window_start is None:
                    # Start new window
                    current_window_start = max(current_hour, start)
            else:
                if current_window_start is not None:
                    # End current window
                    window_end = min(current_hour, end)
                    if window_end > current_window_start:
                        windows.append({
                            'start': current_window_start,
                            'end': window_end
                        })
                    current_window_start = None

            current_hour += timedelta(hours=1)

        # Close final window if open
        if current_window_start is not None:
            windows.append({
                'start': current_window_start,
                'end': end
            })

        return windows

    # =========================================================================
    # STRESS STRATEGY
    # =========================================================================

    def _generate_stress(
        self,
        symbol: str,
        periods: List[PeriodAnalysis],
        count: int,
        max_ticks: Optional[int]
    ) -> List[ScenarioCandidate]:
        """
        Generate stress test scenarios.

        Selects periods with highest volatility AND tick density.

        Args:
            symbol: Trading symbol
            periods: Available periods
            count: Number of scenarios
            max_ticks: Max ticks per scenario

        Returns:
            List of stress test candidates
        """
        vol_pct = self._config.stress.volatility_percentile
        density_pct = self._config.stress.density_percentile

        # Calculate thresholds
        atrs = [p.atr for p in periods]
        densities = [p.tick_density for p in periods]

        atr_threshold = np.percentile(atrs, vol_pct)
        density_threshold = np.percentile(densities, density_pct)

        # Filter for extreme conditions
        stress_periods = [
            p for p in periods
            if p.atr >= atr_threshold and p.tick_density >= density_threshold
        ]

        # If not enough, relax one constraint
        if len(stress_periods) < self._config.stress.min_periods:
            vLog.warning(
                f"Only {len(stress_periods)} periods meet both criteria. "
                f"Relaxing density threshold."
            )
            stress_periods = [
                p for p in periods
                if p.atr >= atr_threshold
            ]

        # Sort by combined score
        stress_periods = sorted(
            stress_periods,
            key=lambda p: (p.atr_percentile +
                           (p.tick_density / max(densities))),
            reverse=True
        )

        # Convert to candidates
        scenarios = []
        for period in stress_periods[:count]:
            candidate = self._period_to_candidate(
                symbol, period, max_ticks
            )
            scenarios.append(candidate)

        return scenarios

    # =========================================================================
    # HELPER METHODS
    # =========================================================================

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
        if self._template_path.exists():
            with open(self._template_path, 'r') as f:
                config = json.load(f)
        else:
            config = self._get_default_template()

        # Update metadata
        config['version'] = "1.0"
        config['scenario_set_name'] = filename.replace('.json', '')
        config['created'] = datetime.now(timezone.utc).isoformat()

        # Add scenarios
        scenarios = result.get_all_scenarios()
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

    def _get_default_template(self) -> Dict:
        """
        Get default template when file not found.

        Returns:
            Default config dictionary
        """
        return {
            "version": "1.0",
            "scenario_set_name": "",
            "created": "",
            "global": {
                "data_mode": "realistic",
                "strategy_config": {
                    "decision_logic_type": "CORE/aggressive_trend",
                    "worker_instances": {
                        "rsi_fast": "CORE/rsi",
                        "envelope_main": "CORE/envelope"
                    },
                    "workers": {
                        "rsi_fast": {
                            "periods": {"M5": 14},
                        },
                        "envelope_main": {
                            "periods": {"M5": 20},
                            "deviation": 0.02,
                        }
                    },
                    "decision_logic_config": {
                        "rsi_oversold": 30,
                        "rsi_overbought": 70,
                        "min_confidence": 0.6
                    }
                },
                "execution_config": {
                    "parallel_workers": False,
                    "worker_parallel_threshold_ms": 1.0,
                    "adaptive_parallelization": True,
                    "log_performance_stats": True
                },
                "trade_simulator_config": {
                    "broker_config_path": "./configs/brokers/mt5/ic_markets_demo.json",
                    "initial_balance": 10000,
                    "currency": "EUR"
                }
            },
            "scenarios": []
        }
