"""
FiniexTestingIDE - Data Index CLI
Command-line tools for tick data import and inspection

Usage:
    python python/cli/data_index_cli.py import [--override]
    python python/cli/data_index_cli.py tick_data_report [BROKER_TYPE]
    python python/cli/data_index_cli.py inspect BROKER_TYPE SYMBOL [TIMEFRAME]

Import configuration is driven by configs/import_config.json (with user_config override).
Offsets are applied automatically per broker_type from the offset registry.
"""

import sys
import traceback
from pathlib import Path
from typing import Optional

from python.configuration.import_config_manager import ImportConfigManager
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
    For gap analysis, use discoveries_cli.py coverage
    """

    def __init__(self):
        """Initialize CLI with config managers."""
        self._import_config = ImportConfigManager()
        self.index_manager = TickIndexManager()
        self.bar_index_manager = BarsIndexManager()

    def cmd_import(self, override: bool = False):
        """
        Import tick data from JSON to Parquet with UTC conversion.
        Offsets are applied automatically per broker_type from import_config.json.

        Args:
            override: If True, overwrite existing Parquet files
        """
        print("\n" + "="*80)
        print("📥 Tick Data Import")
        print("="*80)
        print(f"Override Mode:  {'ENABLED' if override else 'DISABLED'}")
        print(
            f"Move Files:     {'YES' if self._import_config.get_move_processed_files() else 'NO'}")
        print(
            f"Auto Bars:      {'YES' if self._import_config.get_auto_render_bars() else 'NO'}")

        # Display offset registry
        registry = self._import_config.get_offset_registry()
        if registry:
            print(f"Offset Registry:")
            for bt, entry in registry.items():
                offset = entry.get('default_offset_hours', 0)
                desc = entry.get('description', '')
                if offset != 0:
                    print(f"   {bt}: {offset:+d}h — {desc}")
                    print(f"   ⚠️  Times for {bt} will be converted to UTC!")
                else:
                    print(f"   {bt}: {offset:+d}h — {desc}")
        else:
            print(f"Offset Registry: EMPTY (no offsets configured)")

        print("="*80 + "\n")

        # Build offset registry as flat dict {broker_type: offset_hours}
        offset_flat = {
            bt: entry.get('default_offset_hours', 0)
            for bt, entry in registry.items()
        }

        importer = TickDataImporter(
            source_dir=self._import_config.get_data_raw_path(),
            target_dir=self._import_config.get_import_output_path(),
            override=override,
            offset_registry=offset_flat,
            move_processed_files=self._import_config.get_move_processed_files(),
            finished_dir=self._import_config.get_data_finished_path(),
            auto_render_bars=self._import_config.get_auto_render_bars(),
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
    import [--override]
                        Import tick data from JSON to Parquet.
                        Configuration is driven by configs/import_config.json.
                        Offsets are applied automatically per broker_type.
                        --override: Overwrite existing Parquet files

    tick_data_report [BROKER_TYPE]
                        Data Loader Summary Report
                        Optional: filter by broker_type

    inspect BROKER_TYPE SYMBOL [TIMEFRAME]
                        Inspect tick or bar data (metadata, schema, sample)

    help                Show this help

Examples:
    python python/cli/data_index_cli.py import
    python python/cli/data_index_cli.py import --override
    python python/cli/data_index_cli.py tick_data_report
    python python/cli/data_index_cli.py tick_data_report mt5
    python python/cli/data_index_cli.py inspect mt5 EURUSD
    python python/cli/data_index_cli.py inspect kraken_spot BTCUSD M5

Configuration:
    configs/import_config.json      - Base import config (offset registry, paths, processing)
    user_config/import_config.json  - User overrides (optional, deep-merged)

Related CLIs:
    tick_index_cli.py      - Tick index management (rebuild, status, coverage, files)
    discoveries_cli.py     - Gap analysis (coverage build, show, validate, status)
    bar_index_cli.py       - Bar index management (rebuild, status, report, render)
""")


def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        print("❌ Missing command. Use 'help' for usage.")
        sys.exit(1)

    cli = DataIndexCLI()
    command = sys.argv[1].lower()

    try:
        if command == 'import':
            override = '--override' in sys.argv
            cli.cmd_import(override=override)

        elif command == 'tick_data_report':
            broker_type = sys.argv[2] if len(sys.argv) > 2 else None
            cli.cmd_tick_data_report(broker_type=broker_type)

        elif command == 'inspect':
            if len(sys.argv) < 4:
                print("❌ Usage: inspect BROKER_TYPE SYMBOL [TIMEFRAME]")
                print("   Example: inspect mt5 EURUSD M5")
                sys.exit(1)

            broker_type = sys.argv[2]
            symbol = sys.argv[3]
            timeframe = sys.argv[4] if len(sys.argv) > 4 else None

            cli.cmd_inspect(broker_type, symbol, timeframe)

        elif command == 'help':
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


if __name__ == '__main__':
    main()
