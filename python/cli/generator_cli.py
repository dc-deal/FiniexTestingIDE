"""
Generator CLI
==============
Command-line interface for block generation.

Commands:
- generate: Generate scenario configs based on data coverage analysis
"""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from python.framework.utils.time_utils import ensure_utc_aware
from python.scenario.generator.scenario_generator import ScenarioGenerator
from python.scenario.generator.scenario_generator_config_saver import ScenarioGeneratorConfigSaver
from python.framework.types.scenario_types.scenario_generator_types import (
    GenerationResult,
    GenerationStrategy,
)
from python.framework.logging.bootstrap_logger import get_global_logger

vLog = get_global_logger()


class GeneratorCli:
    """
    CLI handler for block generation.
    """

    def __init__(self):
        """Initialize CLI handler."""
        pass

    # =========================================================================
    # GENERATE COMMAND
    # =========================================================================

    def cmd_generate(
        self,
        broker_type: str,
        symbols: List[str],
        count: Optional[int] = None,
        block_size: Optional[int] = None,
        sessions: Optional[str] = None,
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
            count: Number of blocks (max limit, None=all)
            block_size: Block size in hours
            sessions: Comma-separated session filters
            start: Start date filter (ISO format)
            end: End date filter (ISO format)
            output: Output filename
            max_ticks: Max ticks per scenario
        """
        # Parse session filters
        sessions_list: Optional[List[str]] = None
        if sessions:
            sessions_list = [s.strip() for s in sessions.split(',')]

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
                strategy=GenerationStrategy.BLOCKS,
                count=count,
                block_hours=block_size,
                sessions_filter=sessions_list,
                start_filter=start_dt,
                end_filter=end_dt,
                max_ticks=max_ticks
            )

            # Save config
            output_file = output or self._generate_output_name(symbols)

            saver = ScenarioGeneratorConfigSaver()
            config_path = saver.save_config(result, output_file)

            # Print summary
            self._print_generation_summary(result, config_path)

        except Exception as e:
            print(f"❌ Generation failed: {e}")
            vLog.error(f"Generation failed: {e}")
            raise

    def _generate_output_name(self, symbols: List[str]) -> str:
        """
        Generate output filename from symbols.

        Args:
            symbols: List of symbols

        Returns:
            Filename string
        """
        if len(symbols) == 1:
            symbol_part = symbols[0]
        else:
            symbol_part = f"multi_{len(symbols)}"

        timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')
        return f"{symbol_part}_blocks_{timestamp}.json"

    def _print_generation_summary(self, result: GenerationResult, config_path: Path) -> None:
        """
        Print generation result summary.

        Args:
            result: GenerationResult
            config_path: Path to saved config
        """
        print('\n' + '=' * 60)
        print(f"✅ Generated {len(result.scenarios)} blocks")
        print('=' * 60)

        print(f"\nSymbol:     {result.symbol}")
        print(f"Strategy:   {result.strategy.value}")

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

        print(f"\n📂 Config saved to: {config_path}")

        print("\nℹ️  Next steps:")
        print(f"   • View config: cat {config_path}")
        print(f"   • Run test:    python strategy_runner.py")
        print('=' * 60 + '\n')


# =============================================================================
# MAIN ENTRY POINT
# =============================================================================

def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description='Generator CLI — block generation for backtesting scenarios',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    subparsers = parser.add_subparsers(dest='command', help='Commands')

    # ─────────────────────────────────────────────────────────────────────────
    # GENERATE command
    # ─────────────────────────────────────────────────────────────────────────
    generate_parser = subparsers.add_parser(
        'generate',
        help='Generate block-based scenario configurations'
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
        '--count',
        type=int,
        default=None,
        help='Max number of blocks to generate (None=all blocks)'
    )
    generate_parser.add_argument(
        '--block-size',
        type=int,
        default=None,
        help='Max block size in hours (default: 6, min: 1)'
    )
    generate_parser.add_argument(
        '--sessions',
        type=str,
        default=None,
        help='Comma-separated sessions (e.g., sydney_tokyo,london,new_york)'
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

    cli = GeneratorCli()

    if args.command == 'generate':
        cli.cmd_generate(
            broker_type=args.broker_type,
            symbols=args.symbols,
            count=args.count,
            block_size=args.block_size,
            sessions=args.sessions,
            start=args.start,
            end=args.end,
            output=args.output,
            max_ticks=args.max_ticks
        )


if __name__ == '__main__':
    main()
