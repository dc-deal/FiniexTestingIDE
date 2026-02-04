"""
FiniexTestingIDE - Data Index CLI
Command-line tools for tick data import and inspection

Usage:
    python python/cli/data_index_cli.py import [--override] [--time-offset +N/-N --offset-broker TYPE]
    python python/cli/data_index_cli.py tick_data_report [BROKER_TYPE]
    python python/cli/data_index_cli.py inspect BROKER_TYPE SYMBOL [TIMEFRAME]

REFACTORED: 
- Tick index commands moved to tick_index_cli.py (rebuild, status, coverage, files)
- Gap/validate commands moved to coverage_report_cli.py (gaps, validate)
- This CLI now focuses on: import, reports, inspection
"""

import sys
import traceback
from pathlib import Path
from typing import Optional

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
    Command-line interface for data import and inspection.

    Focused responsibilities:
    - Import tick data from JSON to Parquet
    - Generate tick data summary reports
    - Inspect tick/bar data structure

    For index management, use tick_index_cli.py
    For gap analysis, use coverage_report_cli.py
    """

    def __init__(self):
        """Initialize CLI with paths from AppConfigManager."""
        self._app_config = AppConfigManager()
        self.index_manager = TickIndexManager()
        self.bar_index_manager = BarsIndexManager()

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
        inspector.print_inspection(result)

        if timeframe:
            # Inspect bars
            result = inspector.inspect_bars(broker_type, symbol, timeframe)
            inspector.print_inspection(result)

    def cmd_help(self):
        """Show help."""
        print("""
📥 Data Index CLI - Usage

Commands:
    import [--override] [--time-offset +N/-N --offset-broker TYPE]
                        Import tick data from JSON to Parquet
                        --override: Overwrite existing files
                        --time-offset: UTC offset (REQUIRES explicit +/- sign!)
                        --offset-broker: Apply offset only to this broker_type
                                    REQUIRED if --time-offset is set!

    tick_data_report [BROKER_TYPE]
                        Data Loader Summary Report
                        Optional: filter by broker_type

    inspect BROKER_TYPE SYMBOL [TIMEFRAME]
                        Inspect tick or bar data (metadata, schema, sample)

    help                Show this help

Examples:
    python python/cli/data_index_cli.py import
    python python/cli/data_index_cli.py import --time-offset -3 --offset-broker mt5
    python python/cli/data_index_cli.py import --override --time-offset -3 --offset-broker mt5
    python python/cli/data_index_cli.py tick_data_report
    python python/cli/data_index_cli.py tick_data_report mt5
    python python/cli/data_index_cli.py inspect mt5 EURUSD
    python python/cli/data_index_cli.py inspect kraken_spot BTCUSD M5

⚠️  IMPORTANT: --time-offset REQUIRES explicit sign (+/-) to prevent accidents!

Related CLIs:
    tick_index_cli.py      - Tick index management (rebuild, status, coverage, files)
    coverage_report_cli.py - Gap analysis (build, show, validate, status)
    bar_index_cli.py       - Bar index management (rebuild, status, report, render)
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
    """Main entry point."""
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

        elif command == "tick_data_report":
            broker_type = sys.argv[2] if len(sys.argv) > 2 else None
            cli.cmd_tick_data_report(broker_type=broker_type)

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

        # === LEGACY REDIRECTS ===
        elif command == "rebuild":
            print("⚠️  'rebuild' moved to tick_index_cli.py")
            print("   Run: python python/cli/tick_index_cli.py rebuild")
            sys.exit(1)

        elif command == "status":
            print("⚠️  'status' moved to tick_index_cli.py")
            print("   Run: python python/cli/tick_index_cli.py status")
            sys.exit(1)

        elif command == "coverage":
            print("⚠️  'coverage' moved to tick_index_cli.py")
            print(
                "   Run: python python/cli/tick_index_cli.py coverage BROKER_TYPE SYMBOL")
            sys.exit(1)

        elif command == "files":
            print("⚠️  'files' moved to tick_index_cli.py")
            print(
                "   Run: python python/cli/tick_index_cli.py files BROKER_TYPE SYMBOL --start DATE --end DATE")
            sys.exit(1)

        elif command == "gaps":
            print("⚠️  'gaps' moved to coverage_report_cli.py")
            print(
                "   Run: python python/cli/coverage_report_cli.py show BROKER_TYPE SYMBOL")
            sys.exit(1)

        elif command == "validate":
            print("⚠️  'validate' moved to coverage_report_cli.py")
            print("   Run: python python/cli/coverage_report_cli.py validate")
            sys.exit(1)

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
