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

from python.framework.reporting.market_analyzer_report import MarketAnalyzer
from python.framework.utils.time_utils import ensure_utc_aware
from python.scenario.generator.scenario_generator import ScenarioGenerator
from python.framework.types.scenario_generator_types import (
    GenerationResult,
    GenerationStrategy,
    SymbolAnalysis,
    TradingSession,
    VolatilityRegime,
)
from python.framework.reporting.market_report import print_analysis_report
from python.framework.reporting.comparison_report import print_cross_instrument_ranking
from python.framework.utils.activity_volume_provider import get_activity_provider
from python.framework.logging.bootstrap_logger import get_global_logger

vLog = get_global_logger()


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
        broker_type: str,
        symbol: str,
        timeframe: Optional[str] = None
    ) -> None:
        """
        Analyze market data and print report with cross-instrument comparison.

        Args:
            broker_type: Broker type identifier (e.g., 'mt5', 'kraken_spot')
            symbol: Symbol to analyze
            timeframe: Timeframe override
        """
        # Analyze requested symbol
        try:
            analysis = self._analyzer.analyze_symbol(
                broker_type, symbol, timeframe)
            print_analysis_report(analysis)
        except Exception as e:
            print(f"❌ Failed to analyze {symbol}: {e}")
            vLog.error(f"Analysis failed for {symbol}: {e}")
            return

        # Load all other symbols for cross-instrument comparison (same broker_type)
        all_symbols = self._analyzer.list_symbols(broker_type)
        all_analyses: List[SymbolAnalysis] = [analysis]

        for sym in all_symbols:
            if sym == symbol:
                continue  # Already analyzed
            try:
                sym_analysis = self._analyzer.analyze_symbol(
                    broker_type, sym, timeframe)
                all_analyses.append(sym_analysis)
            except Exception as e:
                vLog.warning(f"Could not analyze {sym} for comparison: {e}")

        # Print cross-instrument ranking
        if len(all_analyses) > 1:
            config = self._analyzer.get_config()
            top_count = config.cross_instrument_ranking.top_count
            print_cross_instrument_ranking(all_analyses, symbol, top_count)

    # =========================================================================
    # GENERATE COMMAND
    # =========================================================================

    def cmd_generate(
        self,
        broker_type: str,
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
            broker_type: Broker type identifier (e.g., 'mt5', 'kraken_spot')
            symbols: List of symbols
            strategy: Generation strategy (balanced, blocks, stress)
            count: Number of scenarios
            block_size: Block size in hours (for blocks strategy)
            session: Single session filter
            sessions: Comma-separated session filters
            start: Start date filter (ISO format)
            end: End date filter (ISO format)
            output: Output filename
            max_ticks: Max ticks per scenario
        """
        try:
            gen_strategy = GenerationStrategy(strategy)
        except ValueError:
            print(f"❌ Invalid strategy: {strategy}")
            return

        # Parse session filters
        sessions_list: Optional[List[str]] = None
        if sessions:
            sessions_list = [s.strip() for s in sessions.split(',')]
        elif session:
            sessions_list = [session]

        # Parse date filters
        start_dt: Optional[datetime] = None
        end_dt: Optional[datetime] = None
        if start:
            start_dt = ensure_utc_aware(datetime.fromisoformat(start))
        if end:
            end_dt = ensure_utc_aware(datetime.fromisoformat(end))
        try:
            generator = ScenarioGenerator()

            result = generator.generate(
                broker_type=broker_type,
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

    def _print_generation_summary(self, result: GenerationResult, config_path: Path) -> None:
        """
        Print generation result summary.

        Args:
            result: GenerationResult
            config_path: Path to saved config
        """
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
        'broker_type',
        help='Broker type (e.g., mt5, kraken_spot)'
    )
    analyze_parser.add_argument(
        'symbol',
        help='Symbol to analyze (e.g., EURUSD, BTCUSD)'
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
        'broker_type',
        help='Broker type (e.g., mt5, kraken_spot)'
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
            broker_type=args.broker_type,
            symbol=args.symbol,
            timeframe=args.timeframe
        )

    elif args.command == 'generate':
        cli.cmd_generate(
            broker_type=args.broker_type,
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
