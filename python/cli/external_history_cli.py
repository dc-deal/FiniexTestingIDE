"""
FiniexTestingIDE - External History CLI
Bring a venue's published trade history into the archive as `external` data (#561)

Usage:
    python python/cli/external_history_cli.py verify  --fetch DIR
    python python/cli/external_history_cli.py list    --fetch DIR
    python python/cli/external_history_cli.py convert --fetch DIR --symbols ETHUSD[,BTCUSD…]
                                                      [--raw-dir DIR] [--dry-run]
    python python/cli/external_history_cli.py archive --fetch DIR --zip NAME [--record FILE]

DIR is one fetch folder of the external history store, e.g.
data/external/kraken/trades/2026-10-13. Between `convert` and `archive` runs the ordinary import:
python python/cli/data_index_cli.py import --only ticks
"""

import argparse
import sys
from pathlib import Path

from python.data_management.importers.external.kraken_history_commands import (
    KrakenHistoryCommands,
)


class ExternalHistoryCli:
    """Parameter reception for the external history steps; the steps live in their commands."""

    def run(self, argv=None) -> int:
        """
        Parse the arguments and run one step.

        Args:
            argv: Arguments, default the process's own

        Returns:
            Exit code
        """
        parser = argparse.ArgumentParser(description='External trade history (#561)')
        commands = parser.add_subparsers(dest='command', required=True)

        verify = commands.add_parser('verify', help='check the parts against the vendor checksums')
        verify.add_argument('--fetch', required=True, type=Path)

        listing = commands.add_parser('list', help='member, size and cut per collected symbol')
        listing.add_argument('--fetch', required=True, type=Path)

        convert = commands.add_parser('convert', help='collector-shaped files into the inbox')
        convert.add_argument('--fetch', required=True, type=Path)
        convert.add_argument('--symbols', required=True,
                             help='comma-separated, e.g. ETHUSD,BTCUSD')
        convert.add_argument('--raw-dir', type=Path, default=None)
        convert.add_argument('--dry-run', action='store_true')

        archive = commands.add_parser('archive', help='prove the import, zip, remove copies')
        archive.add_argument('--fetch', required=True, type=Path)
        archive.add_argument('--zip', required=True, dest='zip_name')
        archive.add_argument('--record', type=Path, default=None)

        args = parser.parse_args(argv)
        steps = KrakenHistoryCommands(args.fetch)
        if args.command == 'verify':
            return steps.verify()
        if args.command == 'list':
            return steps.list_pairs()
        if args.command == 'convert':
            return steps.convert([symbol.strip() for symbol in args.symbols.split(',')],
                                 raw_dir=args.raw_dir, dry_run=args.dry_run)
        return steps.archive(args.zip_name, record_path=args.record)


if __name__ == '__main__':
    sys.exit(ExternalHistoryCli().run())
