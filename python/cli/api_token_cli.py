"""
FiniexTestingIDE — API Token CLI

Mint a consumer token for the HTTP API, and show what is configured.

    python python/cli/api_token_cli.py mint --consumer viewer --grants 'bars:*' 'brokers:*'
    python python/cli/api_token_cli.py list
"""

import argparse
from typing import List

from python.configuration.api_token_manager import ApiTokenManager


class ApiTokenCli:
    """Entry point for the API token commands."""

    def __init__(self):
        self._manager = ApiTokenManager()

    def cmd_mint(self, consumer: str, grants: List[str], note: str) -> None:
        """
        Mint one token and show the operator what to do with it.

        Args:
            consumer: The consumer's name
            grants: What it may reach
            note: One line recording who holds it
        """
        print(self._manager.render_mint(consumer, grants, note))

    def cmd_list(self) -> None:
        """Show which consumers are configured, and never their tokens."""
        registry = self._manager.build_registry()
        print(self._manager.describe(registry))
        for name in registry.names():
            print(f'  {name:<16} {registry.grants_of(name):<40} {registry.note_of(name)}')


def main() -> None:
    """Parse arguments and dispatch."""
    parser = argparse.ArgumentParser(description='API consumer tokens')
    subparsers = parser.add_subparsers(dest='command', help='Commands')

    mint_parser = subparsers.add_parser('mint', help='Mint a token for one consumer')
    mint_parser.add_argument('--consumer', required=True, help='Consumer name')
    mint_parser.add_argument(
        '--grants', required=True, nargs='+',
        help="What it may reach, e.g. 'bars:*' 'brokers:kraken_spot'")
    mint_parser.add_argument('--note', default='', help='Who holds this token')

    subparsers.add_parser('list', help='Show configured consumers (never their tokens)')

    args = parser.parse_args()
    cli = ApiTokenCli()

    if args.command == 'mint':
        cli.cmd_mint(args.consumer, args.grants, args.note)
    elif args.command == 'list':
        cli.cmd_list()
    else:
        parser.print_help()


if __name__ == '__main__':
    main()
