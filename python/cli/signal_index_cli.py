"""
FiniexTestingIDE - Signal Index CLI
Command-line tools for signal data import and inspection (#429).

Usage:
    python python/cli/signal_index_cli.py import [--override]
    python python/cli/signal_index_cli.py status
    python python/cli/signal_index_cli.py rebuild
    python python/cli/signal_index_cli.py inspect DATA_SENTIMENT_TYPE SYMBOL
    python python/cli/signal_index_cli.py connect-check

Paths are driven by configs/import_config.json → 'signal_paths' (with user_configs override):
raw JSONL under data/raw/signals/<pipeline_id>/, processed parquet + index under
data/processed/signals/<pipeline_id>/, imported JSONL archived to
data/finished/signals/<pipeline_id>/ (switched by 'processing.move_processed_files').

An import without --override reads the raw directory only, so a re-run costs nothing once
everything is archived. With --override the finished archive is read as well and rebuilt.
"""

import argparse
import sys
import traceback

import pandas as pd

from python.configuration.import_config_manager import ImportConfigManager
from python.configuration.sentiment_config_manager import SentimentConfigManager
from python.data_management.importers.signal_data_importer import SignalDataImporter
from python.data_management.index.signal_index_manager import SignalIndexManager
from python.framework.logging.bootstrap_logger import get_global_logger
from python.framework.signal_data.producer.signal_connect_check import (
    print_connect_check,
    run_connect_check,
)
from python.framework.signal_data.producer.signal_stream_probe import (
    DEFAULT_PROBE_SECONDS,
    print_stream_probe,
    run_stream_probe,
)
from python.framework.types.signal_data_types import (
    SIGNAL_ENVELOPE_SYMBOL,
    SIGNAL_RUNTIME_COLUMNS,
    SignalParquetColumn,
)

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
        finished_dir = self._import_config.get_signal_data_finished_path() \
            if self._import_config.get_move_processed_files() else None

        print('\n' + '=' * 80)
        print('📡 Signal Data Import')
        print('=' * 80)
        print(f'Source:         {source_dir}')
        print(f'Target:         {target_dir}')
        print(f"Finished:       {finished_dir or 'DISABLED (files stay in source)'}")
        print(f"Override Mode:  {'ENABLED' if override else 'DISABLED'}")
        print('=' * 80)

        importer = SignalDataImporter(
            source_dir=source_dir, target_dir=target_dir, override=override,
            finished_dir=finished_dir)
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

    def cmd_connect_check(self):
        """Probe the configured producer: reachable, and which credential answered."""
        manager = SentimentConfigManager()
        config = manager.get_config()
        # The pipeline comes from whichever transport is enabled. Reading poll's id on a
        # stream-only installation would probe an empty name and report a healthy producer
        # for a session that cannot start.
        pipeline_id = (config.stream.pipeline_id if config.stream.enabled
                       else config.poll.pipeline_id)
        result = run_connect_check(
            producer=manager.resolve_active_producer(),
            pipeline_id=pipeline_id,
            timeout_s=config.poll.request_timeout_s)
        print_connect_check(result)
        return result

    def cmd_stream_probe(self, seconds: float):
        """Hold the producer's stream open briefly and report what arrived."""
        manager = SentimentConfigManager()
        result = run_stream_probe(
            producer=manager.resolve_active_producer(),
            stream_config=manager.get_config().stream,
            logger=vLog,
            seconds=seconds)
        print_stream_probe(result)
        return result

    def cmd_inspect(self, data_sentiment_type: str, symbol: str):
        """
        Inspect one signal source/symbol: coverage, parquet structure, quality, sample.

        Args:
            data_sentiment_type: Source identity (= pipeline_id)
            symbol: Trading symbol
        """
        manager = SignalIndexManager(
            data_dir=self._import_config.get_signal_import_output_path())
        manager.build_index()

        coverage = manager.get_symbol_file_coverage(data_sentiment_type, symbol)
        print('\n' + '=' * 80)
        print(f'📡 Inspect Sentiment: {data_sentiment_type} / {symbol}')
        print('=' * 80)
        if not coverage:
            print('   (no data for this source/symbol — import it first)')
            return

        print(f"Files:   {coverage['num_files']}")
        print(f"Rows:    {coverage['total_rows']:,}")
        print(f"Range:   {coverage['start_time']} → {coverage['end_time']}")
        print(f"Name(s): {', '.join(coverage['files'])}")

        # Load all of the symbol's parquet buckets for structure + distributions.
        entries = manager.index[data_sentiment_type][symbol]
        frames = [pd.read_parquet(e['path']) for e in entries]
        df = pd.concat(frames, ignore_index=True) if len(frames) > 1 else frames[0]

        # Column manifest — runtime projection vs. traceability scalar, with dtype.
        print('\nParquet columns (lean projection):')
        for col in SignalParquetColumn:
            tag = 'runtime' if col.value in SIGNAL_RUNTIME_COLUMNS else 'traceability'
            dtype = df[col.value].dtype if col.value in df.columns else '—'
            print(f'   {col.value:16s} {str(dtype):8s} [{tag}]')

        # Row composition — envelope sentinels ('*') vs. the symbol's own rows.
        sentinels = int(
            (df[SignalParquetColumn.SYMBOL.value] == SIGNAL_ENVELOPE_SYMBOL).sum())
        sym_rows = df[df[SignalParquetColumn.SYMBOL.value] == symbol]
        print('\nRow composition:')
        print(f"   Envelope sentinels ('*'): {sentinels:,}")
        print(f'   {symbol} rows:            {len(sym_rows):,}')

        # Signal-quality picture — basis distribution + breaking count (the Fehlerbild lens).
        print(f'\n{symbol} basis distribution:')
        for basis, count in sym_rows[SignalParquetColumn.BASIS.value].value_counts().items():
            label = basis if basis else '(absent/synthesized)'
            print(f'   {label:24s} {count:,}')
        breaking = int(sym_rows[SignalParquetColumn.IS_BREAKING.value].sum())
        print(f'   is_breaking=True:        {breaking:,}')

        # Sample rows — including the new fields (basis, prompt provenance).
        cols = [
            SignalParquetColumn.COLLECTED_MSC.value, SignalParquetColumn.SIGNAL.value,
            SignalParquetColumn.SENTIMENT_SCORE.value, SignalParquetColumn.CONFIDENCE.value,
            SignalParquetColumn.URGENCY.value, SignalParquetColumn.IS_BREAKING.value,
            SignalParquetColumn.BASIS.value, SignalParquetColumn.STATUS.value,
            SignalParquetColumn.PROMPT_VERSION.value, SignalParquetColumn.PROMPT_HASH.value,
        ]
        print('\nSample rows:')
        print(sym_rows[cols].head(5).to_string(index=False))
        print('=' * 80)


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
    # CONNECT-CHECK command
    # ─────────────────────────────────────────────────────────────────────────
    subparsers.add_parser(
        'connect-check',
        help='Probe the configured producer (free routes only, never POST /run)')

    # ─────────────────────────────────────────────────────────────────────────
    # STREAM-PROBE command
    # ─────────────────────────────────────────────────────────────────────────
    stream_parser = subparsers.add_parser(
        'stream-probe',
        help='Hold the producer stream open briefly and print what arrived (#468)')
    stream_parser.add_argument(
        '--seconds', type=float, default=DEFAULT_PROBE_SECONDS,
        help='How long to hold the connection')

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
        elif args.command == 'connect-check':
            if not cli.cmd_connect_check().is_ok():
                sys.exit(1)
        elif args.command == 'stream-probe':
            if not cli.cmd_stream_probe(seconds=args.seconds).ok:
                sys.exit(1)
        elif args.command == 'inspect':
            cli.cmd_inspect(
                data_sentiment_type=args.data_sentiment_type,
                symbol=args.symbol)

    except KeyboardInterrupt:
        print('\n\n👋 Interrupted by user')
        sys.exit(0)
    except Exception as e:
        print(f'\n❌ Error: {e}')
        traceback.print_exc()
        sys.exit(1)


if __name__ == '__main__':
    main()
