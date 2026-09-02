"""
FiniexTestingIDE - AutoTrader CLI
Command-line interface for FiniexAutoTrader live trading sessions.

Usage:
    python python/cli/autotrader_cli.py run --config configs/autotrader_profiles/backtesting/mock_session_test.json
"""

import argparse
import sys
import traceback

from python.configuration.autotrader.autotrader_config_loader import load_autotrader_config
from python.framework.autotrader.autotrader_main import AutotraderMain


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='FiniexAutoTrader — Live trading CLI',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    subparsers = parser.add_subparsers(dest='command', help='Commands')

    # ─────────────────────────────────────────────────────────────────────────
    # RUN command
    # ─────────────────────────────────────────────────────────────────────────
    run_parser = subparsers.add_parser(
        'run', help='Start an AutoTrader live session')
    run_parser.add_argument(
        '--config', required=True,
        help='Path to autotrader config JSON (e.g., configs/autotrader_profiles/backtesting/mock_session_test.json)')
    run_parser.add_argument(
        '--display', action='store_true',
        help='Force enable live console dashboard (overrides config display.enabled)')
    run_parser.add_argument(
        '--delay', type=int, metavar='MS',
        help='Override tick_delay_ms for mock tick source (e.g. --delay 1)')
    run_parser.add_argument(
        '--attended', action='store_true',
        help='Declare that a human is watching this start, so cold-start adoption may ASK '
             '(#355). Without it, adoption_mode=operator_confirm refuses instead of prompting '
             '— a TTY does not prove anybody is reading it (this project\'s own container '
             'allocates one), and a bot waiting forever at 03:00 has simply stopped.')

    # ─────────────────────────────────────────────────────────────────────────
    # Parse and execute
    # ─────────────────────────────────────────────────────────────────────────
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    try:
        if args.command == 'run':
            print('\n' + '=' * 60)
            print('🤖 FiniexAutoTrader')
            print('=' * 60)
            print(f'Config: {args.config}')
            print('=' * 60 + '\n')

            config = load_autotrader_config(args.config)
            if args.display:
                config.display.enabled = True
            if args.delay is not None:
                config.tick_source.tick_delay_ms = args.delay
            trader = AutotraderMain(config, attended=args.attended)
            result = trader.run()

            # The result carries the graded outcome; the CLI only maps it (#372)
            sys.exit(result.get_exit_code())

    except KeyboardInterrupt:
        print('\n\n👋 Interrupted by user')
        sys.exit(0)
    except Exception as e:
        print(f'\n❌ Error: {e}')
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
