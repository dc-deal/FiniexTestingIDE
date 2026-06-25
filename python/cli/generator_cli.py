"""
Generator CLI
==============
Command-line interface for scenario generation. Parameter reception only — all orchestration
lives in GenerationCoordinator.

Commands:
- generate-blocks: Generate block-based scenario configs
- generate-profile: Generate a single profile artifact
- generate-all-profiles: Generate profiles for all symbols across all brokers
"""

import argparse
import sys

from python.scenario.generator.generation_coordinator import GenerationCoordinator


# =============================================================================
# MAIN ENTRY POINT
# =============================================================================

def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description='Generator CLI — scenario generation for backtesting',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    subparsers = parser.add_subparsers(dest='command', help='Commands')

    # ─────────────────────────────────────────────────────────────────────────
    # GENERATE-BLOCKS command
    # ─────────────────────────────────────────────────────────────────────────
    generate_parser = subparsers.add_parser(
        'generate-blocks',
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
    generate_parser.add_argument(
        '--oos-split',
        type=float,
        default=None,
        help='Robustness mode (#367): trailing Out-of-Sample fraction (e.g. 0.3). '
             'When set, writes the set-wide robustness block + time-ordered IS/OOS roles'
    )

    # ─────────────────────────────────────────────────────────────────────────
    # GENERATE-PROFILE command
    # ─────────────────────────────────────────────────────────────────────────
    profile_parser = subparsers.add_parser(
        'generate-profile',
        help='Generate a profile artifact with ATR-minima splitting'
    )
    profile_parser.add_argument(
        'broker_type',
        help='Broker type (e.g., mt5, kraken_spot)'
    )
    profile_parser.add_argument(
        'symbol',
        help='Trading symbol (single, e.g., EURUSD)'
    )
    profile_parser.add_argument(
        '--start',
        type=str,
        required=True,
        help='Start date (ISO format, required)'
    )
    profile_parser.add_argument(
        '--end',
        type=str,
        required=True,
        help='End date (ISO format, required)'
    )
    profile_parser.add_argument(
        '--mode',
        type=str,
        choices=['volatility_split', 'continuous'],
        default='volatility_split',
        help='Generation mode (default: volatility_split)'
    )
    profile_parser.add_argument(
        '--output',
        type=str,
        default=None,
        help='Output filename'
    )

    # ─────────────────────────────────────────────────────────────────────────
    # GENERATE-ALL-PROFILES command
    # ─────────────────────────────────────────────────────────────────────────
    all_profiles_parser = subparsers.add_parser(
        'generate-all-profiles',
        help='Generate profiles for all symbols across all brokers'
    )
    all_profiles_parser.add_argument(
        '--mt5-start',
        type=str,
        required=True,
        help='Start date for mt5 symbols (ISO format)'
    )
    all_profiles_parser.add_argument(
        '--mt5-end',
        type=str,
        required=True,
        help='End date for mt5 symbols (ISO format)'
    )
    all_profiles_parser.add_argument(
        '--kraken-spot-start',
        type=str,
        required=True,
        help='Start date for kraken_spot symbols (ISO format)'
    )
    all_profiles_parser.add_argument(
        '--kraken-spot-end',
        type=str,
        required=True,
        help='End date for kraken_spot symbols (ISO format)'
    )
    all_profiles_parser.add_argument(
        '--mode',
        type=str,
        choices=['volatility_split', 'continuous'],
        default='volatility_split',
        help='Generation mode (default: volatility_split)'
    )

    # ─────────────────────────────────────────────────────────────────────────
    # Parse and execute
    # ─────────────────────────────────────────────────────────────────────────
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    coordinator = GenerationCoordinator()

    if args.command == 'generate-blocks':
        coordinator.generate_blocks(
            broker_type=args.broker_type,
            symbols=args.symbols,
            count=args.count,
            block_size=args.block_size,
            start=args.start,
            end=args.end,
            output=args.output,
            max_ticks=args.max_ticks,
            oos_split=args.oos_split
        )
    elif args.command == 'generate-profile':
        coordinator.generate_profile(
            broker_type=args.broker_type,
            symbol=args.symbol,
            start=args.start,
            end=args.end,
            mode=args.mode,
            output=args.output,
        )
    elif args.command == 'generate-all-profiles':
        broker_starts = {
            'mt5': args.mt5_start,
            'kraken_spot': args.kraken_spot_start,
        }
        broker_ends = {
            'mt5': args.mt5_end,
            'kraken_spot': args.kraken_spot_end,
        }
        coordinator.generate_all_profiles(
            broker_starts=broker_starts,
            broker_ends=broker_ends,
            mode=args.mode,
        )


if __name__ == '__main__':
    main()
