"""
FiniexTestingIDE - Data Index CLI
Command-line tools for Parquet index management

NEW (C#002): Manual index management and gap analysis

Usage:
    python python/cli/data_index_cli.py rebuild
    python python/cli/data_index_cli.py status
    python python/cli/data_index_cli.py coverage EURUSD
    python python/cli/data_index_cli.py gaps EURUSD
    python python/cli/data_index_cli.py files EURUSD --start "2025-09-23" --end "2025-09-24"
"""

import sys
from pathlib import Path
from datetime import datetime
import traceback
import pandas as pd

from python.components.logger.bootstrap_logger import setup_logging
from python.data_worker.data_loader.parquet_index import ParquetIndexManager

vLog = setup_logging(name="DataIndexCLI")


class DataIndexCLI:
    """
    Command-line interface for Parquet index management.
    """

    def __init__(self, data_dir: str = "./data/processed/"):
        """Initialize CLI"""
        self.data_dir = Path(data_dir)
        self.index_manager = ParquetIndexManager(self.data_dir)

    def cmd_rebuild(self):
        """Rebuild index from scratch"""
        print("\n🔄 Rebuilding Parquet index...")
        self.index_manager.build_index(force_rebuild=True)
        self.index_manager.print_summary()

    def cmd_status(self):
        """Show index status"""
        self.index_manager.build_index()  # Load if exists

        print("\n" + "="*60)
        print("📋 Index Status")
        print("="*60)

        if self.index_manager.index_file.exists():
            mtime = datetime.fromtimestamp(
                self.index_manager.index_file.stat().st_mtime
            )
            print(f"Index file:  {self.index_manager.index_file}")
            print(f"Last update: {mtime.strftime('%Y-%m-%d %H:%M:%S')}")
        else:
            print("Index file:  (not found)")

        print(f"Symbols:     {len(self.index_manager.index)}")

        total_files = sum(len(files)
                          for files in self.index_manager.index.values())
        print(f"Total files: {total_files}")

        print("="*60 + "\n")

        if self.index_manager.needs_rebuild():
            print("⚠️  Index is outdated - run 'rebuild' to update\n")

    def cmd_coverage(self, symbol: str):
        """Show coverage statistics for symbol"""
        self.index_manager.build_index()

        try:
            coverage = self.index_manager.get_symbol_coverage(symbol)

            print("\n" + "="*60)
            print(f"📊 Coverage: {symbol}")
            print("="*60)
            print(f"Files:       {coverage['num_files']}")
            print(f"Ticks:       {coverage['total_ticks']:,}")
            print(f"Size:        {coverage['total_size_mb']:.1f} MB")
            print(f"Start:       {coverage['start_time']}")
            print(f"End:         {coverage['end_time']}")
            print("\nFiles:")
            for file in coverage['files']:
                print(f"   • {file}")
            print("="*60 + "\n")

        except ValueError as e:
            print(f"\n❌ Error: {e}\n")

    def cmd_gaps(self, symbol: str):
        """Analyze and report gaps for symbol"""
        self.index_manager.build_index()

        try:
            self.index_manager.print_coverage_report(symbol)
        except ValueError as e:
            print(f"\n❌ Error: {e}\n")

    def cmd_files(self, symbol: str, start: str = None, end: str = None):
        """Show files selected for time range"""
        self.index_manager.build_index()

        if not start or not end:
            print("\n❌ Error: --start and --end required\n")
            print("Example: files EURUSD --start '2025-09-23' --end '2025-09-24'")
            return

        try:
            start_dt = pd.to_datetime(start)
            end_dt = pd.to_datetime(end)

            files = self.index_manager.get_relevant_files(
                symbol, start_dt, end_dt)

            print("\n" + "="*60)
            print(f"📁 File Selection: {symbol}")
            print("="*60)
            print(f"Time range:  {start} → {end}")
            print(f"Selected:    {len(files)} files")
            print("\nFiles:")
            for file in files:
                print(f"   • {file.name}")
            print("="*60 + "\n")

        except Exception as e:
            print(f"\n❌ Error: {e}\n")

    def cmd_validate(self):
        """Validate all symbols and show gaps"""
        self.index_manager.build_index()

        print("\n" + "="*60)
        print("🔍 Validating All Symbols")
        print("="*60 + "\n")

        for symbol in self.index_manager.list_symbols():
            try:
                report = self.index_manager.get_coverage_report(symbol)

                if report.has_issues():
                    print(f"\n⚠️  {symbol}: {report.gap_counts['moderate']} moderate, "
                          f"{report.gap_counts['large']} large gaps")
                else:
                    print(f"✅ {symbol}: No issues")

            except Exception as e:
                print(f"❌ {symbol}: Error - {e} \n{traceback.format_exc()}")

        print("\n" + "="*60)
        print("Use 'gaps SYMBOL' for detailed gap analysis")
        print("="*60 + "\n")

    def cmd_help(self):
        """Show help"""
        print("""
📚 Data Index CLI - Usage

Commands:
    rebuild             Rebuild index from Parquet files
    status              Show index status and metadata
    coverage SYMBOL     Show coverage statistics for symbol
    gaps SYMBOL         Analyze and report gaps for symbol
    files SYMBOL --start DATE --end DATE
                        Show files selected for time range
    validate            Validate all symbols and show issues
    help                Show this help

Examples:
    python python/cli/data_index_cli.py rebuild
    python python/cli/data_index_cli.py status
    python python/cli/data_index_cli.py coverage EURUSD
    python python/cli/data_index_cli.py gaps EURUSD
    python python/cli/data_index_cli.py files EURUSD --start "2025-09-23" --end "2025-09-24"
    python python/cli/data_index_cli.py validate
""")


def main():
    """Main entry point"""
    if len(sys.argv) < 2:
        print("❌ Missing command. Use 'help' for usage.")
        sys.exit(1)

    cli = DataIndexCLI()
    command = sys.argv[1].lower()

    try:
        if command == "rebuild":
            cli.cmd_rebuild()

        elif command == "status":
            cli.cmd_status()

        elif command == "coverage":
            if len(sys.argv) < 3:
                print("❌ Missing symbol. Usage: coverage SYMBOL")
                sys.exit(1)
            cli.cmd_coverage(sys.argv[2])

        elif command == "gaps":
            if len(sys.argv) < 3:
                print("❌ Missing symbol. Usage: gaps SYMBOL")
                sys.exit(1)
            cli.cmd_gaps(sys.argv[2])

        elif command == "files":
            if len(sys.argv) < 6:
                print("❌ Usage: files SYMBOL --start DATE --end DATE")
                sys.exit(1)

            symbol = sys.argv[2]
            start = None
            end = None

            for i, arg in enumerate(sys.argv):
                if arg == "--start" and i + 1 < len(sys.argv):
                    start = sys.argv[i + 1]
                elif arg == "--end" and i + 1 < len(sys.argv):
                    end = sys.argv[i + 1]

            cli.cmd_files(symbol, start, end)

        elif command == "validate":
            cli.cmd_validate()

        elif command == "help":
            cli.cmd_help()

        else:
            print(f"❌ Unknown command: {command}")
            print("Use 'help' for usage.")
            sys.exit(1)

    except KeyboardInterrupt:
        print("\n\n👋 Interrupted by user")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        sys.exit(1)


if __name__ == "__main__":
    main()
