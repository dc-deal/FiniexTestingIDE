"""
FiniexTestingIDE — API Token CLI

Mint a consumer token for the HTTP API, print an account entry for it to act for, and show what
is configured. Nothing here writes a file: both commands print a block for the operator to paste.

    python python/cli/api_token_cli.py account --id analyst --kind person \
        --display-name 'Analyst'
    python python/cli/api_token_cli.py mint --consumer viewer --account analyst \
        --grants 'bars:*' 'brokers:*'
    python python/cli/api_token_cli.py list
"""

import argparse
from typing import List

from python.configuration.api_auth.api_account_manager import ApiAccountManager
from python.configuration.api_auth.api_token_manager import ApiTokenManager
from python.configuration.app_config_manager import AppConfigManager
from python.framework.types.config_types.api_auth_config_types import AccountKind
from python.framework.utils.declared_id_utils import DECLARED_ID_MAX_LENGTH


class ApiTokenCli:
    """Entry point for the API token commands."""

    def __init__(self):
        self._manager = ApiTokenManager()

    def cmd_mint(self, consumer: str, grants: List[str], account: str, note: str) -> None:
        """
        Mint one token and show the operator what to do with it.

        Args:
            consumer: The consumer's name
            grants: What it may reach
            account: The account it acts for
            note: One line recording who holds it
        """
        print(self._manager.render_mint(consumer, grants, account, note))

    def cmd_account(self, account_id: str, kind: str, display_name: str, note: str) -> None:
        """
        Print one account entry for the operator to paste.

        Args:
            account_id: The id tokens will name
            kind: `person` or `service`
            display_name: How a human reads it
            note: One line on who or what this is
        """
        print(ApiAccountManager().render_account(account_id, kind, display_name, note))

    def cmd_list(self) -> None:
        """Show which consumers are configured, and never their tokens."""
        registry = self._manager.build_registry()
        identities = self._manager.get_identities()
        print(self._manager.describe(registry, AppConfigManager().get_api_require_auth()))
        for name in registry.names():
            print(f'  {name:<16} {identities[name].account.account_id:<12} '
                  f'{registry.grants_of(name):<40} {registry.note_of(name)}')


def main() -> None:
    """Parse arguments and dispatch."""
    parser = argparse.ArgumentParser(description='API consumer tokens')
    subparsers = parser.add_subparsers(dest='command', help='Commands')

    mint_parser = subparsers.add_parser('mint', help='Mint a token for one consumer')
    mint_parser.add_argument('--consumer', required=True, help='Consumer name')
    mint_parser.add_argument(
        '--account', required=True,
        help='The account the token acts for, e.g. analyst (see the account command)')
    mint_parser.add_argument(
        '--grants', required=True, nargs='+',
        help="What it may reach, e.g. 'bars:*' 'brokers:kraken_spot'")
    mint_parser.add_argument('--note', default='', help='Who holds this token')

    account_parser = subparsers.add_parser(
        'account', help='Print an account entry to paste (writes nothing)')
    account_parser.add_argument(
        '--id', required=True, dest='account_id',
        help=f'Up to {DECLARED_ID_MAX_LENGTH} characters of a-z, 0-9 and hyphen; '
             f'never "operator"')
    account_parser.add_argument(
        '--kind', required=True, choices=[kind.value for kind in AccountKind],
        help='person, or service for a machine consumer such as a sibling project')
    account_parser.add_argument('--display-name', required=True, help='How a human reads it')
    account_parser.add_argument('--note', default='', help='Who or what this is')

    subparsers.add_parser('list', help='Show configured consumers (never their tokens)')

    args = parser.parse_args()
    cli = ApiTokenCli()

    if args.command == 'mint':
        cli.cmd_mint(args.consumer, args.grants, args.account, args.note)
    elif args.command == 'account':
        cli.cmd_account(args.account_id, args.kind, args.display_name, args.note)
    elif args.command == 'list':
        cli.cmd_list()
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
