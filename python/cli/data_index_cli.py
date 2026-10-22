"""
FiniexTestingIDE - Data Index CLI
Command-line tools for tick data import and inspection

Usage:
    python python/cli/data_index_cli.py import [--override] [--only ticks|signals]
                                               [--include-finished] [--no-display]
    python python/cli/data_index_cli.py tick-data-report [BROKER_TYPE]
    python python/cli/data_index_cli.py inspect BROKER_TYPE SYMBOL [TIMEFRAME]

One import covers both inboxes — tick exports, then signal JSONL — and ends with the bars
and the discovery caches. Import configuration is driven by configs/import_config.json (with
user_configs override); each broker's server clock (market_config.json) converts its times.
"""

import argparse
import sys
import traceback

from python.data_management.importers.import_coordinator import (
    IMPORT_EXIT_CODES,
    ImportCoordinator,
    import_paths_from_config,
)
from python.data_management.importers.tick_data_reporter import run_summary_report
from python.data_management.index.bars_index_manager import BarsIndexManager
from python.data_management.index.data_inspector import DataInspector
from python.data_management.index.tick_index_manager import TickIndexManager
from python.framework.logging.bootstrap_logger import get_global_logger
from python.framework.types.import_types.import_progress_types import ImportSection
from python.framework.types.run_origin_types import RunChannel

vLog = get_global_logger()


class DataIndexCli:
    """
    Command-line interface for data import and inspection.

    Focused responsibilities:
    - Import tick data from JSON to Parquet
    - Generate tick data summary reports
    - Inspect tick/bar data structure
    """

    def __init__(self):
        """Initialize CLI with config managers."""
        self.index_manager = TickIndexManager()
        self.bar_index_manager = BarsIndexManager()

    def cmd_import(self, override: bool = False, only: str = None,
                   include_finished: bool = False, show_display: bool = True) -> int:
        """
        Import both inboxes: tick exports, then signal JSONL.

        Args:
            override: If True, overwrite existing Parquet files
            only: 'ticks' or 'signals' to import one archive only, None for both
            include_finished: If True, also re-read the finished signal archive
            show_display: If False, log line by line instead of the live screen

        Returns:
            The process exit code for the import's outcome
        """
        coordinator = ImportCoordinator(
            paths=import_paths_from_config(),
            override=override,
            only=ImportSection(only) if only else None,
            include_finished=include_finished,
            show_display=show_display,
            channel=RunChannel.CLI,
        )
        return IMPORT_EXIT_CODES[coordinator.run()]

    def cmd_tick_data_report(self, broker_type: str = None):
        """
        Generate tick data summary report.

        Args:
            broker_type: Optional filter for specific broker_type.
                        If None, shows all broker_types.
        """
        print('\n' + '=' * 60)
        print('📊 Tick Data Report')
        if broker_type:
            print(f'   Filter: {broker_type}')
        print('=' * 60)

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


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='Data import and inspection CLI',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    subparsers = parser.add_subparsers(dest='command', help='Commands')

    # ─────────────────────────────────────────────────────────────────────────
    # IMPORT command
    # ─────────────────────────────────────────────────────────────────────────
    import_parser = subparsers.add_parser(
        'import', help='Import the tick and signal inboxes, render bars, rebuild caches')
    import_parser.add_argument(
        '--override', action='store_true', default=False,
        help='Overwrite existing Parquet files')
    import_parser.add_argument(
        '--only', choices=[ImportSection.TICKS.value, ImportSection.SIGNALS.value],
        default=None, help='Import one archive only')
    import_parser.add_argument(
        '--include-finished', action='store_true', default=False,
        help='Also re-read the finished signal archive, re-projecting every day ever imported')
    import_parser.add_argument(
        '--no-display', action='store_true', default=False,
        help='Log line by line instead of showing the live screen')

    # ─────────────────────────────────────────────────────────────────────────
    # TICK-DATA-REPORT command
    # ─────────────────────────────────────────────────────────────────────────
    report_parser = subparsers.add_parser(
        'tick-data-report', help='Data Loader Summary Report')
    report_parser.add_argument(
        'broker_type', nargs='?', default=None,
        help='Optional: filter by broker_type')

    # ─────────────────────────────────────────────────────────────────────────
    # INSPECT command
    # ─────────────────────────────────────────────────────────────────────────
    inspect_parser = subparsers.add_parser(
        'inspect', help='Inspect tick or bar data (metadata, schema, sample)')
    inspect_parser.add_argument(
        'broker_type', help='Broker type identifier')
    inspect_parser.add_argument(
        'symbol', help='Trading symbol')
    inspect_parser.add_argument(
        'timeframe', nargs='?', default=None,
        help='Optional timeframe for bar inspection')

    # ─────────────────────────────────────────────────────────────────────────
    # Parse and execute
    # ─────────────────────────────────────────────────────────────────────────
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    cli = DataIndexCli()

    try:
        if args.command == 'import':
            sys.exit(cli.cmd_import(
                override=args.override,
                only=args.only,
                include_finished=args.include_finished,
                show_display=not args.no_display,
            ))

        elif args.command == 'tick-data-report':
            cli.cmd_tick_data_report(broker_type=args.broker_type)

        elif args.command == 'inspect':
            cli.cmd_inspect(
                broker_type=args.broker_type,
                symbol=args.symbol,
                timeframe=args.timeframe
            )

    except KeyboardInterrupt:
        print('\n\n👋 Interrupted by user')
        sys.exit(0)
    except Exception as e:
        print(f'\n❌ Error: {e}')
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
