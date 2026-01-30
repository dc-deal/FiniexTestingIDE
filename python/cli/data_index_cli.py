"""
FiniexTestingIDE - Data Index CLI
Command-line tools for Parquet index management and tick data import

Import command with UTC offset support (explicit sign required)

Usage - see below
"""

import sys
from pathlib import Path
from datetime import datetime
import traceback
from typing import Optional
import pandas as pd

from python.configuration.app_config_manager import AppConfigManager
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

    def __init__(self):
        """Initialize CLI with paths from AppConfigManager."""
        self._app_config = AppConfigManager()
        self.data_dir = Path(self._app_config.get_data_processed_path())
        self.index_manager = TickIndexManager(self.data_dir)
        self.bar_index_manager = BarsIndexManager(self.data_dir)

    def cmd_import(self, override: bool = False, time_offset: int = 0, offset_broker: Optional[str] = None):
        """
        Import tick data from JSON to Parquet with UTC conversion.

        Args:
            override: If True, overwrite existing Parquet files
            time_offset: Manual UTC offset in hours (e.g., -3 for GMT+3 → UTC)
            offset_broker: Apply offset only to files with this broker_type
        """
        print("\n" + "="*80)
        print("📥 Tick Data Import")
        print("="*80)
        print(f"Override Mode: {'ENABLED' if override else 'DISABLED'}")

        if time_offset != 0:
            print(
                f"Time Offset:   {time_offset:+d} hours (for broker_type='{offset_broker}')")
            print("\n⚠️  WARNING: After offset ALL TIMES WILL BE UTC!")
            print("⚠️  Sessions will be RECALCULATED based on UTC time!")
        else:
            print("Time Offset:   NONE")

        print("="*80 + "\n")

        importer = TickDataImporter(
            source_dir=self._app_config.get_data_raw_path(),
            target_dir=self._app_config.get_data_processed_path(),
            override=override,
            time_offset=time_offset,
            offset_broker=offset_broker
        )

        importer.process_all_exports()

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
            print(
                f"Broker Types: {', '.join(self.index_manager.list_broker_types())}")
            print(f"Symbols:      {len(self.index_manager.list_symbols())}")
        else:
            print("Index file:  (not found)")

        print(f"Symbols:     {len(self.index_manager.index)}")

        total_files = sum(len(files)
                          for files in self.index_manager.index.values())
        print(f"Total files: {total_files}")

        print("="*60 + "\n")

        if self.index_manager.needs_rebuild():
            print("⚠️  Index is outdated - run 'rebuild' to update\n")

    def cmd_tick_data_report(self, broker_type: str = None):
        """
        Generate tick data summary report.

        Args:
            broker_type: Optional filter for specific broker_type.
                        If None, shows all broker_types.
        """
        print("\n" + "=" * 60)
        print("📊 Tick Data Report")
        if broker_type:
            print(f"   Filter: {broker_type}")
        print("=" * 60)

        run_summary_report(broker_type=broker_type)

    def cmd_coverage(self, broker_type: str, symbol: str):
        """
        Show coverage statistics for symbol.

        Args:
            broker_type: Broker type identifier
            symbol: Trading symbol
        """
        self.index_manager.build_index()

        try:
            coverage = self.index_manager.get_symbol_coverage(
                broker_type, symbol)

            print("\n" + "="*60)
            print(f"📊 Coverage: {broker_type}/{symbol}")
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

    def cmd_gaps(self, broker_type: str, symbol: str):
        """
        Analyze and report gaps for symbol.

        Args:
            broker_type: Broker type identifier
            symbol: Trading symbol
        """
        self.index_manager.build_index()

        try:
            self.index_manager.print_coverage_report(broker_type, symbol)
        except ValueError as e:
            print(f"\n❌ Error: {e}\n")

    def cmd_files(self, broker_type: str, symbol: str, start: str = None, end: str = None):
        """
        Show files selected for time range.

        Args:
            broker_type: Broker type identifier
            symbol: Trading symbol
            start: Start date string
            end: End date string
        """
        self.index_manager.build_index()

        if not start or not end:
            print("\n❌ Error: --start and --end required\n")
            print("Example: files EURUSD --start '2025-09-23' --end '2025-09-24'")
            return

        try:
            start_dt = pd.to_datetime(start)
            end_dt = pd.to_datetime(end)

            files = self.index_manager.get_relevant_files(
                broker_type, symbol, start_dt, end_dt)

            print("\n" + "="*60)
            print(f"🔍 File Selection: {broker_type}/{symbol}")
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

        for broker_type in self.index_manager.list_broker_types():
            print(f"\n📂 {broker_type}:")

            for symbol in self.index_manager.list_symbols(broker_type):
                try:
                    report = self.index_manager.get_coverage_report(
                        broker_type, symbol)

                    if report.has_issues():
                        print(f"  ⚠️  {symbol}: {report.gap_counts['moderate']} moderate, "
                              f"{report.gap_counts['large']} large gaps")
                    else:
                        print(f"  ✅ {symbol}: No issues")

                except Exception as e:
                    print(f"  ❌ {symbol}: Error - {e}")

        print("\n" + "="*60)
        print("Use 'gaps BROKER_TYPE SYMBOL' for detailed gap analysis")
        print("="*60 + "\n")

    def cmd_inspect(self, broker_type: str, symbol: str, timeframe: str = None):
        """
        Inspect tick or bar data.

        Args:
            broker_type: Broker type identifier
            symbol: Trading symbol
            timeframe: Optional timeframe for bar inspection
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
        result = inspector.inspect_ticks(broker_type, symbol)
        # Print results
        inspector.print_inspection(result)
        if timeframe:
            # Inspect bars
            result = inspector.inspect_bars(broker_type, symbol, timeframe)
            # Print results
            inspector.print_inspection(result)

    def cmd_help(self):
        """Show help"""
        print("""
            📚 Data Index CLI - Usage

            Commands:
                import [--override] [--time-offset +N/-N --offset-broker TYPE]
                                    Import tick data from JSON to Parquet
                                    --override: Overwrite existing files
                                    --time-offset: UTC offset (REQUIRES explicit +/- sign!)
                                    --offset-broker: Apply offset only to this broker_type
                                                REQUIRED if --time-offset is set!

                rebuild             Rebuild index from Parquet files
                status              Show index status and metadata
                tick_data_report    Data Loader Summary Report & Test Load
                coverage BROKER_TYPE SYMBOL
                                    Show coverage statistics for symbol
                gaps BROKER_TYPE SYMBOL
                                    Analyze and report gaps for symbol
                files BROKER_TYPE SYMBOL --start DATE --end DATE
                                    Show files selected for time range
                validate            Validate all symbols and show issues
                inspect BROKER_TYPE SYMBOL [TIMEFRAME]
                                    Inspect tick or bar data (metadata, schema, sample)
                help                Show this help

            Examples:
                python python/cli/data_index_cli.py import
                python python/cli/data_index_cli.py import --time-offset -3 --offset-broker mt5
                python python/cli/data_index_cli.py import --override --time-offset -3 --offset-broker mt5
                python python/cli/data_index_cli.py rebuild
                python python/cli/data_index_cli.py status
                python python/cli/data_index_cli.py coverage mt5 EURUSD
                python python/cli/data_index_cli.py coverage kraken_spot BTCUSD
                python python/cli/data_index_cli.py gaps mt5 EURUSD
                python python/cli/data_index_cli.py files mt5 EURUSD --start "2025-09-23" --end "2025-09-24"
                python python/cli/data_index_cli.py validate
                python python/cli/data_index_cli.py inspect mt5 EURUSD
                python python/cli/data_index_cli.py inspect kraken_spot BTCUSD M5

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
            override = "--override" in sys.argv
            time_offset = 0
            offset_broker = None

            for i, arg in enumerate(sys.argv):
                if arg == "--time-offset" and i + 1 < len(sys.argv):
                    try:
                        time_offset = parse_time_offset(sys.argv[i + 1])
                    except ValueError as e:
                        print(f"\n❌ ERROR: {e}\n")
                        sys.exit(1)
                elif arg == "--offset-broker" and i + 1 < len(sys.argv):
                    offset_broker = sys.argv[i + 1].lower().strip()

            # Validation: time_offset requires offset_broker
            if time_offset != 0 and offset_broker is None:
                print("\n❌ ERROR: --time-offset requires --offset-broker")
                print("   Example: --time-offset -3 --offset-broker mt5\n")
                sys.exit(1)

            if offset_broker is not None and time_offset == 0:
                print("\n❌ ERROR: --offset-broker requires --time-offset")
                print("   Example: --time-offset -3 --offset-broker mt5\n")
                sys.exit(1)

            cli.cmd_import(override=override, time_offset=time_offset,
                           offset_broker=offset_broker)

        elif command == "rebuild":
            cli.cmd_rebuild()

        elif command == "status":
            cli.cmd_status()

        elif command == "tick_data_report":
            # Optional: filter by broker_type
            broker_type = sys.argv[2] if len(sys.argv) > 2 else None
            cli.cmd_tick_data_report(broker_type=broker_type)

        elif command == "coverage":
            if len(sys.argv) < 4:
                print("❌ Usage: coverage BROKER_TYPE SYMBOL")
                print("   Example: coverage mt5 EURUSD")
                sys.exit(1)
            cli.cmd_coverage(sys.argv[2], sys.argv[3])

        elif command == "gaps":
            if len(sys.argv) < 4:
                print("❌ Usage: gaps BROKER_TYPE SYMBOL")
                print("   Example: gaps mt5 EURUSD")
                sys.exit(1)
            cli.cmd_gaps(sys.argv[2], sys.argv[3])

        elif command == "files":
            if len(sys.argv) < 7:
                print("❌ Usage: files BROKER_TYPE SYMBOL --start DATE --end DATE")
                print(
                    "   Example: files mt5 EURUSD --start '2025-09-23' --end '2025-09-24'")
                sys.exit(1)

            broker_type = sys.argv[2]
            symbol = sys.argv[3]
            start = None
            end = None

            for i, arg in enumerate(sys.argv):
                if arg == "--start" and i + 1 < len(sys.argv):
                    start = sys.argv[i + 1]
                elif arg == "--end" and i + 1 < len(sys.argv):
                    end = sys.argv[i + 1]

            cli.cmd_files(broker_type, symbol, start, end)

        elif command == "validate":
            cli.cmd_validate()

        elif command == "inspect":
            if len(sys.argv) < 4:
                print("❌ Usage: inspect BROKER_TYPE SYMBOL [TIMEFRAME]")
                print("   Example: inspect mt5 EURUSD M5")
                sys.exit(1)

            broker_type = sys.argv[2]
            symbol = sys.argv[3]
            timeframe = sys.argv[4] if len(sys.argv) > 4 else None

            cli.cmd_inspect(broker_type, symbol, timeframe)

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
