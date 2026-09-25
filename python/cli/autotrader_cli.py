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
from python.framework.exceptions.host_identity_errors import HostIdentityError
from python.framework.exceptions.live_execution_errors import (
    OneOffInsideDeploymentError,
)
from python.framework.types.run_origin_types import RunChannel


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

    run_parser.add_argument(
        '--one-off', action='store_true',
        help='Detach THIS start from the profile\'s deployment (#497): its ledger row names '
             'no deployment and joins no history. Narrows the profile, never widens it — a '
             'deployment cannot be declared from the command line, because an unattended '
             'restart re-executes a command nobody typed and would fragment the very history '
             'the declaration exists to hold together.')
    run_parser.add_argument(
        '--new-deployment', action='store_true',
        help='Begin a NEW deployment instead of continuing the one this bot last named '
             '(#497). For a bot redeployed after a pause or with different parameters, where '
             'continuing the old history would claim a continuity that does not exist. Safe '
             'to forget: without it the existing deployment simply continues.')
    run_parser.add_argument(
        '--allow-dirty', action='store_true',
        help='Permit REAL orders from code no commit describes (#551). Without it, a session '
             'whose effective dry_run is false refuses to start unless its code is exactly one '
             'commit: uncommitted changes in this repository or an algo repository refuse, and '
             'so do a strategy under no version control (or in a directory its repository '
             'ignores) and a repository git cannot read — such a run cannot be traced back to '
             'the code that ran. The override is not silent: the run header records it '
             '(origin.allow_dirty), the diff hash of a dirty repository is recorded and its '
             'patch stored where it can be (not for an unversioned or unreadable repository, '
             'one containing a nested repository, or when the store cannot write — the refusal '
             'names each such case), and the '
             'post-run validation reports it as a warning. It never permits code that changes '
             'while the session starts. A mock or dry-run session is never affected.')

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
            trader = AutotraderMain(
                config, attended=args.attended,
                one_off=args.one_off, new_deployment=args.new_deployment,
                channel=RunChannel.CLI, allow_dirty=args.allow_dirty)
            result = trader.run()

            # The result carries the graded outcome; the CLI only maps it (#372)
            sys.exit(result.get_exit_code())

    except KeyboardInterrupt:
        print('\n\n👋 Interrupted by user')
        sys.exit(0)
    except OneOffInsideDeploymentError as refusal:
        # A refusal, not a fault: the message already says what to do instead, and a stack
        # trace under it would suggest something broke. Same exit code as a framework
        # emergency (#372) — the session did not start.
        print(f'\n🔗 {refusal}\n')
        sys.exit(2)
    except HostIdentityError as refusal:
        # The same kind of refusal (#551): the installation's identity file exists but cannot be
        # trusted, and the message names the file and both ways forward. It is raised before
        # the run header is written, because a header must not state an identity nobody trusts.
        print(f'\n🪪 {refusal}\n')
        sys.exit(2)
    except Exception as e:
        print(f'\n❌ Error: {e}')
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
