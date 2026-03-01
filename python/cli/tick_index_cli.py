"""
FiniexTestingIDE - Tick Index CLI
Command-line tools for Tick Index management

Usage:
    python python/cli/tick_index_cli.py rebuild
    python python/cli/tick_index_cli.py status
    python python/cli/tick_index_cli.py file-coverage BROKER_TYPE SYMBOL
    python python/cli/tick_index_cli.py files BROKER_TYPE SYMBOL --start DATE --end DATE
"""

import argparse
import sys
import traceback
from datetime import datetime

import pandas as pd

from python.data_management.index.tick_index_manager import TickIndexManager
from python.framework.logging.bootstrap_logger import get_global_logger

vLog = get_global_logger()


class TickIndexCLI:
    """
    Command-line interface for Tick Index management.

    Provides tools for rebuilding, status checking, and file selection.
    """

    def __init__(self):
        """Initialize CLI."""
        self.index_manager = TickIndexManager()

    def cmd_rebuild(self):
        """Rebuild tick index from scratch."""
        print("\n🔄 Rebuilding Parquet tick index...")
        self.index_manager.build_index(force_rebuild=True)
        self.index_manager.print_summary()

    def cmd_status(self):
        """Show tick index status."""
        self.index_manager.build_index()

        print("\n" + "="*60)
        print("📋 Tick Index Status")
        print("="*60)

        if self.index_manager.index_file.exists():
            mtime = datetime.fromtimestamp(
                self.index_manager.index_file.stat().st_mtime
            )
            print(f"Index file:   {self.index_manager.index_file}")
            print(f"Last update:  {mtime.strftime('%Y-%m-%d %H:%M:%S')}")
            print(
                f"Broker Types: {', '.join(self.index_manager.list_broker_types())}")

            # Count symbols per broker type
            total_symbols = 0
            for bt in self.index_manager.list_broker_types():
                symbols = self.index_manager.list_symbols(bt)
                total_symbols += len(symbols)
            print(f"Symbols:      {total_symbols}")

            # Count total files
            total_files = sum(
                len(files)
                for bt in self.index_manager.index.values()
                for files in bt.values()
            )
            print(f"Total files:  {total_files}")
        else:
            print("Index file:   (not found)")

        print("="*60 + "\n")

        if self.index_manager.needs_rebuild():
            print("⚠️  Index is outdated - run 'rebuild' to update\n")

    def cmd_file_coverage(self, broker_type: str, symbol: str):
        """
        Show coverage statistics for symbol.

        Args:
            broker_type: Broker type identifier
            symbol: Trading symbol
        """
        self.index_manager.build_index()

        try:
            coverage = self.index_manager.get_symbol_file_coverage(
                broker_type, symbol)

            if not coverage:
                print(f"\n❌ No data found for {broker_type}/{symbol}\n")
                return

            print("\n" + "="*60)
            print(f"📊 Coverage: {broker_type}/{symbol}")
            print("="*60)
            print(f"Files:       {coverage['num_files']}")
            print(f"Ticks:       {coverage['total_ticks']:,}")
            print(f"Size:        {coverage['total_size_mb']:.1f} MB")
            print(f"Start:       {coverage['start_time']}")
            print(f"End:         {coverage['end_time']}")
            print('\nFiles:')
            for file in coverage['files']:
                print(f"   • {file}")
            print("="*60 + "\n")

        except Exception as e:
            print(f"\n❌ Error: {e}\n")

    def cmd_files(self, broker_type: str, symbol: str, start: str, end: str):
        """
        Show files selected for time range.

        Args:
            broker_type: Broker type identifier
            symbol: Trading symbol
            start: Start date string
            end: End date string
        """
        self.index_manager.build_index()

        try:
            start_dt = pd.to_datetime(start)
            end_dt = pd.to_datetime(end)

            files = self.index_manager.get_relevant_files(
                broker_type, symbol, start_dt, end_dt
            )

            print("\n" + "="*60)
            print(f"🔍 File Selection: {broker_type}/{symbol}")
            print("="*60)
            print(f"Time range:  {start} → {end}")
            print(f"Selected:    {len(files)} files")
            print('\nFiles:')
            for file in files:
                print(f"   • {file.name}")
            print("="*60 + "\n")

        except Exception as e:
            print(f"\n❌ Error: {e}\n")


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Tick Index management CLI',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    subparsers = parser.add_subparsers(dest='command', help='Commands')

    # ─────────────────────────────────────────────────────────────────────────
    # REBUILD command
    # ─────────────────────────────────────────────────────────────────────────
    subparsers.add_parser(
        'rebuild', help='Rebuild tick index from Parquet files')

    # ─────────────────────────────────────────────────────────────────────────
    # STATUS command
    # ─────────────────────────────────────────────────────────────────────────
    subparsers.add_parser(
        'status', help='Show tick index status and metadata')

    # ─────────────────────────────────────────────────────────────────────────
    # FILE-COVERAGE command
    # ─────────────────────────────────────────────────────────────────────────
    cov_parser = subparsers.add_parser(
        'file-coverage', help='Show coverage statistics for symbol')
    cov_parser.add_argument(
        'broker_type', help='Broker type identifier')
    cov_parser.add_argument(
        'symbol', help='Trading symbol')

    # ─────────────────────────────────────────────────────────────────────────
    # FILES command
    # ─────────────────────────────────────────────────────────────────────────
    files_parser = subparsers.add_parser(
        'files', help='Show files selected for time range')
    files_parser.add_argument(
        'broker_type', help='Broker type identifier')
    files_parser.add_argument(
        'symbol', help='Trading symbol')
    files_parser.add_argument(
        '--start', required=True, help='Start date (ISO format)')
    files_parser.add_argument(
        '--end', required=True, help='End date (ISO format)')

    # ─────────────────────────────────────────────────────────────────────────
    # Parse and execute
    # ─────────────────────────────────────────────────────────────────────────
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    cli = TickIndexCLI()

    try:
        if args.command == 'rebuild':
            cli.cmd_rebuild()

        elif args.command == 'status':
            cli.cmd_status()

        elif args.command == 'file-coverage':
            cli.cmd_file_coverage(args.broker_type, args.symbol)

        elif args.command == 'files':
            cli.cmd_files(args.broker_type, args.symbol, args.start, args.end)

    except KeyboardInterrupt:
        print("\n\n👋 Interrupted by user")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
