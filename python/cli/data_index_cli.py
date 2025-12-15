"""
FiniexTestingIDE - Data Index CLI
Command-line tools for Parquet index management and tick data import

Import command with UTC offset support (explicit sign required)

Usage:
    python python/cli/data_index_cli.py import [--override] [--time-offset +N/-N]
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

from python.data_management.index.bars_index_manager import BarsIndexManager
from python.data_management.index.data_inspector import DataInspector
from python.data_management.index.tick_index_manager import TickIndexManager
from python.data_management.importers.tick_data_report import run_summary_report
from python.data_management.importers.tick_importer import TickDataImporter

from python.framework.logging.bootstrap_logger import get_global_logger
vLog = get_global_logger()


class DataIndexCLI:
    """
    Command-line interface for Parquet index management and data import.
    """

    def __init__(self, data_dir: str = "./data/processed/"):
        """Initialize CLI"""
        self.data_dir = Path(data_dir)
        self.index_manager = TickIndexManager(self.data_dir)
        self.bar_index_manager = BarsIndexManager(self.data_dir)

    def cmd_import(self, override: bool = False, time_offset: int = 0):
        """
        Import tick data from JSON to Parquet with UTC conversion.

        Args:
            override: If True, overwrite existing Parquet files
            time_offset: Manual UTC offset in hours (e.g., -3 for GMT+3 → UTC)
        """
        print("\n" + "="*80)
        print("📥 Tick Data Import")
        print("="*80)
        print(f"Override Mode: {'ENABLED' if override else 'DISABLED'}")
        print(f"Time Offset:   {time_offset:+d} hours" if time_offset !=
              0 else "Time Offset:   NONE")
        if time_offset != 0:
            print("\n⚠️  WARNING: After offset ALL TIMES WILL BE UTC!")
            print("⚠️  Sessions will be RECALCULATED based on UTC time!")
        print("="*80 + "\n")

        importer = TickDataImporter(
            source_dir="./data/raw/",
            target_dir="./data/processed/",
            override=override,
            time_offset=time_offset
        )

        importer.process_all_mql5_exports()

    def cmd_rebuild(self):
        """Rebuild index from scratch"""
        print("\n🔄 Rebuilding Parquet index...")
        self.index_manager.build_index(force_rebuild=True)
        self.index_manager.print_summary()

    def cmd_status(self):
        """Show index status"""
        self.index_manager.build_index()

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

    def cmd_tick_data_report(self):
        """Tick data report"""
        self.index_manager.build_index()

        print("\n" + "="*60)
        print("📊 Tick Data Report")
        print("="*60)

        run_summary_report()

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
            print(f"🔍 File Selection: {symbol}")
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

    def cmd_inspect(self, symbol: str, timeframe: str = None):
        """
        Inspect tick or bar data: metadata, schema, and sample rows.

        Args:
            symbol: Trading symbol
            timeframe: Optional timeframe (if provided, inspect bars instead of ticks)
        """
        # Build indices if needed
        self.index_manager.build_index()
        self.bar_index_manager.build_index()

        # Create inspector
        inspector = DataInspector(
            tick_index_manager=self.index_manager,
            bar_index_manager=self.bar_index_manager
        )

        # Inspect ticks
        result = inspector.inspect_ticks(symbol)
        # Print results
        inspector.print_inspection(result)
        if timeframe:
            # Inspect bars
            result = inspector.inspect_bars(symbol, timeframe)
            # Print results
            inspector.print_inspection(result)

    def cmd_help(self):
        """Show help"""
        print("""
            📚 Data Index CLI - Usage

            Commands:
                import [--override] [--time-offset +N/-N]
                                    Import tick data from JSON to Parquet
                                    --override: Overwrite existing files
                                    --time-offset: UTC offset (REQUIRES explicit +/- sign!)
                                                Valid: +1, -3, +5, -2
                                                INVALID: 1, 3 (missing sign)
                
                rebuild             Rebuild index from Parquet files
                status              Show index status and metadata
                tick_data_report    Data Loader Summary Report & Test Load
                coverage SYMBOL     Show coverage statistics for symbol
                gaps SYMBOL         Analyze and report gaps for symbol
                files SYMBOL --start DATE --end DATE
                                    Show files selected for time range
                validate            Validate all symbols and show issues
                inspect SYMBOL [TIMEFRAME]
                                    Inspect tick or bar data (metadata, schema, sample)
                                    - No timeframe: Inspect ticks
                                    - With timeframe: Inspect bars
                help                Show this help

            Examples:
                python python/cli/data_index_cli.py import
                python python/cli/data_index_cli.py import --time-offset -3
                python python/cli/data_index_cli.py import --time-offset +2
                python python/cli/data_index_cli.py import --override --time-offset -3
                python python/cli/data_index_cli.py rebuild
                python python/cli/data_index_cli.py status
                python python/cli/data_index_cli.py coverage EURUSD
                python python/cli/data_index_cli.py gaps EURUSD
                python python/cli/data_index_cli.py files EURUSD --start "2025-09-23" --end "2025-09-24"
                python python/cli/data_index_cli.py validate
                python python/cli/data_index_cli.py inspect EURUSD
                python python/cli/data_index_cli.py inspect EURUSD M5

            ⚠️  IMPORTANT: --time-offset REQUIRES explicit sign (+/-) to prevent accidents!
            """)


def parse_time_offset(value: str) -> int:
    """
    Parse time offset with MANDATORY explicit sign.

    Args:
        value: Offset string (must start with + or -)

    Returns:
        Integer offset value

    Raises:
        ValueError: If sign is missing or value is invalid
    """
    if not value:
        raise ValueError("Time offset cannot be empty")

    # Check for explicit sign
    if not (value.startswith('+') or value.startswith('-')):
        raise ValueError(
            f"Time offset MUST have explicit sign (+/-): '{value}'\n"
            f"   Valid examples: +1, -3, +5, -2\n"
            f"   Invalid: 1, 3 (missing sign - ambiguous!)\n"
            f"   This is a safety feature to prevent accidental timezone mistakes."
        )

    try:
        return int(value)
    except ValueError:
        raise ValueError(
            f"Invalid time offset format: '{value}' (must be integer)")


def main():
    """Main entry point"""
    if len(sys.argv) < 2:
        print("❌ Missing command. Use 'help' for usage.")
        sys.exit(1)

    cli = DataIndexCLI()
    command = sys.argv[1].lower()

    try:
        if command == "import":
            # Parse import options
            override = "--override" in sys.argv
            time_offset = 0

            for i, arg in enumerate(sys.argv):
                if arg == "--time-offset" and i + 1 < len(sys.argv):
                    try:
                        time_offset = parse_time_offset(sys.argv[i + 1])
                    except ValueError as e:
                        print(f"\n❌ ERROR: {e}\n")
                        sys.exit(1)

            cli.cmd_import(override=override, time_offset=time_offset)

        elif command == "rebuild":
            cli.cmd_rebuild()

        elif command == "status":
            cli.cmd_status()

        elif command == "tick_data_report":
            cli.cmd_tick_data_report()

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

        elif command == "inspect":
            if len(sys.argv) < 3:
                print("❌ Missing symbol. Usage: inspect SYMBOL [TIMEFRAME]")
                sys.exit(1)

            symbol = sys.argv[2]
            timeframe = sys.argv[3] if len(sys.argv) > 3 else None

            cli.cmd_inspect(symbol, timeframe)

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
        traceback.print_exc()
        sys.exit(1)


if __name__ == "__main__":
    main()
