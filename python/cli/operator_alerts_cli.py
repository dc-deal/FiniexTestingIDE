"""
FiniexTestingIDE - Operator Alerts CLI

Proves the out-of-band channels of an AutoTrader session (#235) on the machine that will run it:
one test message, one liveness ping — and, while a bot is being set up, the chat id to put into
its credentials file.

Usage:
    python python/cli/operator_alerts_cli.py check [--profile PATH]
    python python/cli/operator_alerts_cli.py chat-id [--profile PATH]

Without --profile the app_config defaults, with their user_configs override, are checked; with a
profile, the configuration its sessions would run with.
"""

import argparse
import sys

from python.framework.autotrader.operator_alerts.operator_alerts_check import (
    print_bot_chats,
    print_operator_alerts_check,
    resolve_check_settings,
)


def main() -> None:
    """Parse the command and run it."""
    parser = argparse.ArgumentParser(
        description='Check the operator alert channel and the liveness ping of AutoTrader '
                    'sessions.')
    commands = parser.add_subparsers(dest='command', required=True)
    for name, help_text in (
            ('check', 'send one test message and one liveness ping'),
            ('chat-id', 'list the chats that have written to the bot')):
        command = commands.add_parser(name, help=help_text)
        command.add_argument(
            '--profile', default=None,
            help='AutoTrader profile whose configuration to check (default: app_config)')
    args = parser.parse_args()

    settings, speaker, bot_id = resolve_check_settings(args.profile)
    if args.command == 'check':
        ok = print_operator_alerts_check(settings, speaker, bot_id)
    else:
        ok = print_bot_chats(settings)
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
