"""
FiniexTestingIDE - Signal Index CLI
Command-line tools for signal data import and inspection (#429).

Usage:
    python python/cli/signal_index_cli.py import [--override]
    python python/cli/signal_index_cli.py status
    python python/cli/signal_index_cli.py rebuild
    python python/cli/signal_index_cli.py inspect DATA_SENTIMENT_TYPE SYMBOL

Paths are driven by configs/import_config.json → 'signal_paths' (with user_configs override):
raw JSONL under data/raw/signals/<pipeline_id>/, processed parquet + index under
data/processed/signals/<pipeline_id>/.
"""

import argparse
import sys
import traceback

import pandas as pd

from python.configuration.import_config_manager import ImportConfigManager
from python.data_management.importers.signal_importer import SignalDataImporter
from python.data_management.index.signal_index_manager import SignalIndexManager

from python.framework.logging.bootstrap_logger import get_global_logger
vLog = get_global_logger()


class SignalIndexCli:
    """
    Command-line interface for signal data import and inspection (#429).

    - Import signal JSONL to columnar parquet
    - Build / rebuild + summarize the signal index
    - Inspect one source/symbol (coverage + sample rows)
    """

    def __init__(self):
        """Initialize CLI with the import config manager."""
        self._import_config = ImportConfigManager()

    def cmd_import(self, override: bool = False):
        """
        Import signal JSONL to parquet and rebuild the index.

        Args:
            override: If True, overwrite existing parquet files
        """
        source_dir = self._import_config.get_signal_data_raw_path()
        target_dir = self._import_config.get_signal_import_output_path()

        print("\n" + "=" * 80)
        print("📡 Signal Data Import")
        print("=" * 80)
        print(f"Source:         {source_dir}")
        print(f"Target:         {target_dir}")
        print(f"Override Mode:  {'ENABLED' if override else 'DISABLED'}")
        print("=" * 80)

        importer = SignalDataImporter(
            source_dir=source_dir, target_dir=target_dir, override=override)
        importer.process_all_signals()

    def cmd_status(self):
        """Load + summarize the signal index."""
        manager = SignalIndexManager(
            data_dir=self._import_config.get_signal_import_output_path())
        manager.build_index()
        manager.print_summary()

    def cmd_rebuild(self):
        """Force a full rebuild of the signal index."""
        manager = SignalIndexManager(
            data_dir=self._import_config.get_signal_import_output_path())
        manager.build_index(force_rebuild=True)
        manager.print_summary()

    def cmd_inspect(self, data_sentiment_type: str, symbol: str):
        """
        Inspect one signal source/symbol: coverage + sample rows.

        Args:
            data_sentiment_type: Source identity (= pipeline_id)
            symbol: Trading symbol
        """
        manager = SignalIndexManager(
            data_dir=self._import_config.get_signal_import_output_path())
        manager.build_index()

        coverage = manager.get_symbol_file_coverage(data_sentiment_type, symbol)
        print("\n" + "=" * 80)
        print(f"📡 Inspect Sentiment: {data_sentiment_type} / {symbol}")
        print("=" * 80)
        if not coverage:
            print("   (no data for this source/symbol — import it first)")
            return

        print(f"Files:   {coverage['num_files']}")
        print(f"Rows:    {coverage['total_rows']:,}")
        print(f"Range:   {coverage['start_time']} → {coverage['end_time']}")
        print(f"Name(s): {', '.join(coverage['files'])}")

        files = manager.index[data_sentiment_type][symbol]
        df = pd.read_parquet(files[0]['path'])
        sample = df[df['symbol'] == symbol].head(5)
        print("\nSample rows:")
        print(sample[['collected_msc', 'symbol', 'signal',
                      'sentiment_score', 'confidence', 'is_breaking', 'status']].to_string(index=False))
        print("=" * 80)


def main():
    parser = argparse.ArgumentParser(
        description='Signal data import and inspection CLI (#429)',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    subparsers = parser.add_subparsers(dest='command', help='Commands')

    # ─────────────────────────────────────────────────────────────────────────
    # IMPORT command
    # ─────────────────────────────────────────────────────────────────────────
    import_parser = subparsers.add_parser(
        'import', help='Import signal JSONL to parquet + rebuild index')
    import_parser.add_argument(
        '--override', action='store_true', default=False,
        help='Overwrite existing parquet files')

    # ─────────────────────────────────────────────────────────────────────────
    # STATUS command
    # ─────────────────────────────────────────────────────────────────────────
    subparsers.add_parser('status', help='Load + summarize the signal index')

    # ─────────────────────────────────────────────────────────────────────────
    # REBUILD command
    # ─────────────────────────────────────────────────────────────────────────
    subparsers.add_parser('rebuild', help='Force a full signal index rebuild')

    # ─────────────────────────────────────────────────────────────────────────
    # INSPECT command
    # ─────────────────────────────────────────────────────────────────────────
    inspect_parser = subparsers.add_parser(
        'inspect', help='Inspect one signal source/symbol (coverage + sample)')
    inspect_parser.add_argument(
        'data_sentiment_type', help='Source identity (= pipeline_id)')
    inspect_parser.add_argument('symbol', help='Trading symbol')

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    cli = SignalIndexCli()

    try:
        if args.command == 'import':
            cli.cmd_import(override=args.override)
        elif args.command == 'status':
            cli.cmd_status()
        elif args.command == 'rebuild':
            cli.cmd_rebuild()
        elif args.command == 'inspect':
            cli.cmd_inspect(
                data_sentiment_type=args.data_sentiment_type,
                symbol=args.symbol)

    except KeyboardInterrupt:
        print("\n\n👋 Interrupted by user")
        sys.exit(0)
    except Exception as e:
        print(f"\n❌ Error: {e}")
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
