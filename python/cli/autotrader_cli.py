"""
FiniexTestingIDE - AutoTrader CLI
Command-line interface for FiniexAutoTrader live trading sessions.

Usage:
    python python/cli/autotrader_cli.py run --config configs/autotrader_profiles/btcusd_mock.json
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
        help='Path to autotrader config JSON (e.g., configs/autotrader_profiles/btcusd_mock.json)')

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
            trader = AutotraderMain(config)
            result = trader.run()

            # Exit code: 0 for normal, 1 for emergency
            sys.exit(0 if result.shutdown_mode == 'normal' else 1)

    except KeyboardInterrupt:
        print('\n\n👋 Interrupted by user')
        sys.exit(0)
    except Exception as e:
        print(f'\n❌ Error: {e}')
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
