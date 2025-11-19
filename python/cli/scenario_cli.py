"""
Scenario CLI
============
Command-line interface for market analysis and scenario generation.

Commands:
- analyze: Analyze market data and show volatility/activity report
- generate: Generate scenario configs based on analysis

Location: python/cli/scenario_cli.py
"""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from python.scenario.market_analyzer import MarketAnalyzer
from python.scenario.generator import ScenarioGenerator
from python.framework.types.scenario_generator_types import (
    GenerationStrategy,
    SymbolAnalysis,
    TradingSession,
    VolatilityRegime,
)
from python.framework.utils.activity_volume_provider import get_activity_provider
from python.components.logger.bootstrap_logger import get_logger

vLog = get_logger()


class ScenarioCLI:
    """
    CLI handler for scenario analysis and generation.
    """

    def __init__(self, data_dir: str = "./data/processed"):
        """
        Initialize CLI handler.

        Args:
            data_dir: Path to processed data directory
        """
        self._data_dir = Path(data_dir)
        self._analyzer = MarketAnalyzer(str(self._data_dir))
        self._activity_provider = get_activity_provider()

    # =========================================================================
    # ANALYZE COMMAND
    # =========================================================================

    def cmd_analyze(
        self,
        symbols: List[str],
        timeframe: Optional[str] = None,
        all_symbols: bool = False
    ) -> None:
        """
        Analyze market data and print report.

        Args:
            symbols: List of symbols to analyze
            timeframe: Timeframe override
            all_symbols: Analyze all available symbols
        """
        if all_symbols:
            symbols = self._analyzer.list_symbols()

        if not symbols:
            print("❌ No symbols specified. Use --all or provide symbol names.")
            return

        for symbol in symbols:
            try:
                analysis = self._analyzer.analyze_symbol(symbol, timeframe)
                self._print_analysis_report(analysis)
            except Exception as e:
                print(f"❌ Failed to analyze {symbol}: {e}")
                vLog.error(f"Analysis failed for {symbol}: {e}")

    def _print_analysis_report(self, analysis: SymbolAnalysis) -> None:
        """
        Print formatted analysis report.

        Args:
            analysis: SymbolAnalysis results
        """
        # Header
        print("\n" + "=" * 60)
        print(f"📊 MARKET ANALYSIS REPORT: {analysis.symbol}")
        print("=" * 60)

        # Overview
        print(f"Data Range:     {analysis.start_time.strftime('%Y-%m-%d')} → "
              f"{analysis.end_time.strftime('%Y-%m-%d')} ({analysis.total_days} days)")
        print(f"Timeframe:      {analysis.timeframe}")
        print(f"Market Type:    {analysis.market_type}")
        print(f"Data Source:    {analysis.data_source}")

        # Divider
        print("\n" + "─" * 60)
        print("📈 VOLATILITY DISTRIBUTION (ATR-based)")
        print("─" * 60)

        # Total coverage time
        total_periods = len(analysis.periods)
        granularity_hours = 1  # From config - regime_granularity_hours
        total_hours = total_periods * granularity_hours
        total_days = total_hours // 24
        remaining_hours = total_hours % 24
        print(
            f"Total Coverage: {total_days}d {remaining_hours}h ({total_periods} periods)\n")

        # Volatility regimes with duration
        regime_names = {
            VolatilityRegime.VERY_LOW: "Very Low   (0-20%)",
            VolatilityRegime.LOW: "Low        (20-40%)",
            VolatilityRegime.MEDIUM: "Medium     (40-60%)",
            VolatilityRegime.HIGH: "High       (60-80%)",
            VolatilityRegime.VERY_HIGH: "Very High  (80-100%)",
        }

        for regime in VolatilityRegime:
            count = analysis.regime_distribution.get(regime, 0)
            pct = analysis.regime_percentages.get(regime, 0)
            bar_len = int(pct / 10)
            bar = "█" * bar_len + "░" * (10 - bar_len)

            # Calculate duration for this regime
            regime_hours = count * granularity_hours
            regime_days = regime_hours // 24
            regime_rem_hours = regime_hours % 24
            duration_str = f"{regime_days:2d}d {regime_rem_hours:2d}h"

            print(
                f"   {regime_names[regime]}:  {count:4d} periods  {bar}  {pct:5.1f}%  → {duration_str}")

        # ATR stats
        print(f"\n   ATR Range: {analysis.atr_min:.5f} - {analysis.atr_max:.5f} "
              f"(avg: {analysis.atr_avg:.5f})")

        # Session statistics with regime distribution
        print("\n" + "─" * 60)
        print("📊 SESSION ACTIVITY")
        print("─" * 60)

        activity_label = self._activity_provider.get_metric_label(
            analysis.market_type
        ).lower()

        session_names = {
            TradingSession.SYDNEY_TOKYO: "Asian (Sydney/Tokyo)",
            TradingSession.LONDON: "London",
            TradingSession.NEW_YORK: "New York",
            TradingSession.TRANSITION: "Transition",
        }

        # Short regime labels for compact display
        regime_short = {
            VolatilityRegime.VERY_LOW: "VL",
            VolatilityRegime.LOW: "L",
            VolatilityRegime.MEDIUM: "M",
            VolatilityRegime.HIGH: "H",
            VolatilityRegime.VERY_HIGH: "VH",
        }

        for session in TradingSession:
            if session not in analysis.session_summaries:
                continue

            summary = analysis.session_summaries[session]

            # Calculate session duration
            session_hours = summary.period_count * granularity_hours
            session_days = session_hours // 24
            session_rem_hours = session_hours % 24

            print(
                f"\n   {session_names[session]} ({summary.period_count} periods, {session_days}d {session_rem_hours}h):")
            print(f"      Total {activity_label}:    {summary.total_ticks:,}")
            print(
                f"      Avg density:    {summary.avg_tick_density:,.0f} {activity_label}/hour")
            print(
                f"      ATR range:      {summary.min_atr:.5f} - {summary.max_atr:.5f}")

            # Regime distribution for this session
            if summary.period_count > 0:
                regime_parts = []
                for regime in VolatilityRegime:
                    regime_count = summary.regime_distribution.get(regime, 0)
                    regime_pct = (regime_count / summary.period_count) * 100
                    regime_parts.append(
                        f"{regime_short[regime]}: {regime_pct:.0f}%")
                print(f"      Regimes:        {' | '.join(regime_parts)}")

        # Data quality
        print("\n" + "─" * 60)
        print("📦 DATA QUALITY")
        print("─" * 60)
        print(f"   Total bars:      {analysis.total_bars:,}")
        print(f"   Total {activity_label}:    {analysis.total_ticks:,}")
        print(f"   Real bar ratio:  {analysis.real_bar_ratio * 100:.1f}%")

        # Recommendations
        print("\n" + "─" * 60)
        print("💡 GENERATION RECOMMENDATIONS")
        print("─" * 60)
        print(f"   • Balanced testing: --strategy balanced --count 12")
        print(f"   • Chronological:    --strategy blocks --block-size 6")
        print(f"   • Stress testing:   --strategy stress --count 5")
        print(
            f"\n   Run: python scenario_cli.py generate {analysis.symbol} --help")

        print("=" * 60 + "\n")

    # =========================================================================
    # GENERATE COMMAND
    # =========================================================================

    def cmd_generate(
        self,
        symbols: List[str],
        strategy: str = "balanced",
        count: Optional[int] = None,
        block_size: Optional[int] = None,
        session: Optional[str] = None,
        sessions: Optional[str] = None,  # Comma-separated sessions
        start: Optional[str] = None,
        end: Optional[str] = None,
        output: Optional[str] = None,
        max_ticks: Optional[int] = None
    ) -> None:
        """
        Generate scenario configurations.

        Args:
            symbols: List of symbols
            strategy: Generation strategy (balanced, blocks, stress)
            count: Number of scenarios to generate
            block_size: Block size in hours (for blocks strategy)
            session: Filter by trading session
            start: Start date filter (ISO format)
            end: End date filter (ISO format)
            output: Output filename
            max_ticks: Max ticks per scenario
        """
        if not symbols:
            print("❌ No symbols specified.")
            return

        # Parse strategy
        try:
            gen_strategy = GenerationStrategy(strategy.lower())
        except ValueError:
            print(f"❌ Unknown strategy: {strategy}")
            print("   Available: balanced, blocks, stress")
            return

        # Parse dates
        start_dt = None
        end_dt = None
        if start:
            start_dt = datetime.fromisoformat(
                start).replace(tzinfo=timezone.utc)
        if end:
            end_dt = datetime.fromisoformat(end).replace(tzinfo=timezone.utc)

        # Initialize generator
        generator = ScenarioGenerator(str(self._data_dir))

        # Generate scenarios
        try:
            # Parse sessions list for blocks strategy
            sessions_list = None
            if sessions:
                sessions_list = [s.strip() for s in sessions.split(',')]

            result = generator.generate(
                symbols=symbols,
                strategy=gen_strategy,
                count=count,
                block_hours=block_size,
                session_filter=session,
                sessions_filter=sessions_list,
                start_filter=start_dt,
                end_filter=end_dt,
                max_ticks=max_ticks
            )

            # Save config
            output_file = output or self._generate_output_name(
                symbols, gen_strategy
            )

            config_path = generator.save_config(result, output_file)

            # Print summary
            self._print_generation_summary(result, config_path)

        except Exception as e:
            print(f"❌ Generation failed: {e}")
            vLog.error(f"Generation failed: {e}")
            raise

    def _generate_output_name(
        self,
        symbols: List[str],
        strategy: GenerationStrategy
    ) -> str:
        """
        Generate output filename from symbols and strategy.

        Args:
            symbols: List of symbols
            strategy: Generation strategy

        Returns:
            Filename string
        """
        if len(symbols) == 1:
            symbol_part = symbols[0]
        else:
            symbol_part = f"multi_{len(symbols)}"

        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M")
        return f"{symbol_part}_{strategy.value}_{timestamp}.json"

    def _print_generation_summary(self, result, config_path: Path) -> None:
        """
        Print generation result summary.

        Args:
            result: GenerationResult
            config_path: Path to saved config
        """
        from python.framework.types.scenario_generator_types import GenerationStrategy

        print("\n" + "=" * 60)
        print(
            f"✅ Generated {len(result.scenarios)} {'blocks' if result.strategy == GenerationStrategy.BLOCKS else 'scenarios'}")
        print("=" * 60)

        print(f"\nSymbol:     {result.symbol}")
        print(f"Strategy:   {result.strategy.value}")

        if result.strategy == GenerationStrategy.BLOCKS:
            # Time-based summary for blocks
            if result.scenarios:
                first_start = min(s.start_time for s in result.scenarios)
                last_end = max(s.end_time for s in result.scenarios)
                total_hours = sum(
                    (s.end_time - s.start_time).total_seconds() / 3600
                    for s in result.scenarios
                )
                avg_hours = total_hours / len(result.scenarios)

                print(
                    f"Time range: {first_start.strftime('%Y-%m-%d')} → {last_end.strftime('%Y-%m-%d')}")
                print(
                    f"Total:      {total_hours:.0f}h ({avg_hours:.1f}h avg/block)")
        else:
            # Tick-based summary for balanced/stress
            print(f"Total ticks: {result.total_estimated_ticks:,}")
            print(f"Avg/scenario: {result.avg_ticks_per_scenario:,.0f}")

            print("\nRegime coverage:")
            for regime, count in result.regime_coverage.items():
                if count > 0:
                    print(f"   {regime.value}: {count}")

        print(f"\n📂 Config saved to: {config_path}")

        print("\nℹ️  Next steps:")
        print(f"   • View config: cat {config_path}")
        print(f"   • Run test:    python strategy_runner.py")
        print("=" * 60 + "\n")


# =============================================================================
# MAIN ENTRY POINT
# =============================================================================

def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Scenario analysis and generation CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    subparsers = parser.add_subparsers(dest='command', help='Commands')

    # ─────────────────────────────────────────────────────────────────────────
    # ANALYZE command
    # ─────────────────────────────────────────────────────────────────────────
    analyze_parser = subparsers.add_parser(
        'analyze',
        help='Analyze market data for volatility and activity'
    )
    analyze_parser.add_argument(
        'symbols',
        nargs='*',
        help='Symbols to analyze (e.g., EURUSD GBPUSD)'
    )
    analyze_parser.add_argument(
        '--all',
        action='store_true',
        help='Analyze all available symbols'
    )
    analyze_parser.add_argument(
        '--timeframe',
        type=str,
        default=None,
        help='Timeframe to analyze (default: M5)'
    )

    # ─────────────────────────────────────────────────────────────────────────
    # GENERATE command
    # ─────────────────────────────────────────────────────────────────────────
    generate_parser = subparsers.add_parser(
        'generate',
        help='Generate scenario configurations'
    )
    generate_parser.add_argument(
        'symbols',
        nargs='+',
        help='Symbols for scenarios (e.g., EURUSD GBPUSD)'
    )
    generate_parser.add_argument(
        '--strategy',
        type=str,
        default='balanced',
        choices=['balanced', 'blocks', 'stress'],
        help='Generation strategy (default: balanced)'
    )
    generate_parser.add_argument(
        '--count',
        type=int,
        default=None,
        help='Number of scenarios. For blocks: max limit (None=all blocks)'
    )
    generate_parser.add_argument(
        '--block-size',
        type=int,
        default=None,
        help='Max block size in hours (for blocks strategy, default: 6, min: 1)'
    )
    generate_parser.add_argument(
        '--session',
        type=str,
        default=None,
        choices=['sydney_tokyo', 'london', 'new_york'],
        help='Filter by trading session'
    )
    generate_parser.add_argument(
        '--sessions',
        type=str,
        default=None,
        help='Comma-separated sessions for blocks (e.g., sydney_tokyo,london)'
    )
    generate_parser.add_argument(
        '--start',
        type=str,
        default=None,
        help='Start date filter (ISO format)'
    )
    generate_parser.add_argument(
        '--end',
        type=str,
        default=None,
        help='End date filter (ISO format)'
    )
    generate_parser.add_argument(
        '--output',
        type=str,
        default=None,
        help='Output filename'
    )
    generate_parser.add_argument(
        '--max-ticks',
        type=int,
        default=None,
        help='Max ticks per scenario'
    )

    # ─────────────────────────────────────────────────────────────────────────
    # Parse and execute
    # ─────────────────────────────────────────────────────────────────────────
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    cli = ScenarioCLI()

    if args.command == 'analyze':
        cli.cmd_analyze(
            symbols=args.symbols,
            timeframe=args.timeframe,
            all_symbols=args.all
        )

    elif args.command == 'generate':
        cli.cmd_generate(
            symbols=args.symbols,
            strategy=args.strategy,
            count=args.count,
            block_size=args.block_size,
            session=args.session,
            sessions=args.sessions,
            start=args.start,
            end=args.end,
            output=args.output,
            max_ticks=args.max_ticks
        )


if __name__ == "__main__":
    main()
