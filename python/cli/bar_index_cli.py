"""
FiniexTestingIDE - Bar Index CLI
Command-line tools for pre-rendered bar index management and reporting

Usage:
    python python/cli/bar_index_cli.py rebuild
    python python/cli/bar_index_cli.py status
    python python/cli/bar_index_cli.py report
"""

import sys
from pathlib import Path
from datetime import datetime
import traceback

from python.data_worker.data_loader.parquet_bars_index import ParquetBarsIndexManager
from python.framework.reporting.bar_index_report import BarIndexReportGenerator

from python.components.logger.bootstrap_logger import get_logger
from python.framework.utils.activity_volume_provider import get_activity_provider
vLog = get_logger()


class BarIndexCLI:
    """
    Command-line interface for bar index management and reporting.
    """

    def __init__(self, data_dir: str = "./data/processed/"):
        """Initialize CLI"""
        self.data_dir = Path(data_dir)
        self.index_manager = ParquetBarsIndexManager(self.data_dir)

    def cmd_rebuild(self):
        """Rebuild bar index from scratch"""
        print("\n" + "="*80)
        print("🔄 Rebuilding Bar Index")
        print("="*80)
        print(f"Data directory: {self.data_dir}")
        print("="*80 + "\n")

        self.index_manager.build_index(force_rebuild=True)
        self.index_manager.print_summary()

        print("\n✅ Bar index rebuild complete\n")

    def cmd_status(self):
        """Show bar index status and overview"""
        self.index_manager.build_index()

        print("\n" + "="*80)
        print("📊 Bar Index Status")
        print("="*80)

        # Index file info
        if self.index_manager.index_file.exists():
            mtime = datetime.fromtimestamp(
                self.index_manager.index_file.stat().st_mtime
            )
            print(f"Index file:  {self.index_manager.index_file}")
            print(f"Last update: {mtime.strftime('%Y-%m-%d %H:%M:%S')}")
        else:
            print("Index file:  (not found)")

        # Symbol overview
        symbols = self.index_manager.list_symbols()
        print(f"Symbols:     {len(symbols)}")

        if not symbols:
            print("\n⚠️  No bar data found in index")
            print("="*80 + "\n")
            return

        # Count total timeframes across all symbols
        total_timeframes = sum(
            len(self.index_manager.index[symbol])
            for symbol in symbols
        )
        print(f"Timeframes:  {total_timeframes} (across all symbols)")

        print("\n" + "─"*80)
        print("Symbols and Timeframes:")
        print("─"*80)

        # List each symbol with available timeframes
        for symbol in sorted(symbols):
            timeframes = self.index_manager.get_available_timeframes(symbol)
            stats = self.index_manager.get_symbol_stats(symbol)

            # Calculate total bars for this symbol
            total_bars = sum(tf_stats['bar_count']
                             for tf_stats in stats.values())

            print(f"\n{symbol}:")
            print(f"   Timeframes: {', '.join(timeframes)}")
            print(f"   Total bars: {total_bars:,}")

            # Get first timeframe entry for metadata
            first_tf = sorted(timeframes)[0]
            first_entry = self.index_manager.index[symbol][first_tf]

            # Show metadata if available
            market_type = first_entry.get('market_type', 'unknown')
            source_version_min = first_entry.get(
                'source_version_min', 'unknown')
            source_version_max = first_entry.get(
                'source_version_max', 'unknown')
            data_source = first_entry.get('data_source', 'unknown')

            # Version display
            if source_version_min == source_version_max:
                version_str = source_version_min
            else:
                version_str = f"{source_version_min} - {source_version_max}"

            print(f"   Source:     {data_source} (v{version_str})")
            print(f"   Market:     {market_type}")

            # Activity metrics using provider
            provider = get_activity_provider()
            activity_label = provider.get_metric_label(market_type)

            for tf in sorted(timeframes):
                tf_stats = stats[tf]

                # Get full entry for activity data
                entry = self.index_manager.index[symbol][tf]
                total_activity = provider.get_total_activity_value(
                    entry, market_type)
                avg_activity = provider.get_avg_activity_value(
                    entry, market_type)

                print(f"      • {tf}: {tf_stats['bar_count']:,} bars "
                      f"({tf_stats['file_size_mb']:.1f} MB) "
                      f"[{activity_label}: {provider.format_activity_value(total_activity, market_type)}, "
                      f"Ø {provider.format_activity_value(avg_activity, market_type)}/bar]")

        print("\n" + "="*80)

        if self.index_manager.needs_rebuild():
            print("⚠️  Index is outdated - run 'rebuild' to update")

        print()

    def cmd_report(self):
        """Generate detailed bar index report and save to framework/reports"""
        self.index_manager.build_index()

        print("\n" + "="*80)
        print("📋 Generating Bar Index Report")
        print("="*80 + "\n")

        # Generate report
        report_gen = BarIndexReportGenerator(self.index_manager)
        report_path = report_gen.generate_report()

        print(f"\n✅ Report saved to: {report_path}")
        print("="*80 + "\n")

    def cmd_render(self, clean: bool = False):
        """Render bars from tick data"""
        from python.data_worker.importer.bar_importer import BarImporter

        print("\n" + "="*80)
        print("🔄 Bar Rendering")
        print("="*80)
        print(
            f"Clean Mode: {'ENABLED (delete all bars first)' if clean else 'DISABLED (skip symbols without ticks)'}")
        print("="*80 + "\n")

        try:
            bar_importer = BarImporter(str(self.data_dir))
            bar_importer.render_bars_for_all_symbols(
                data_collector="mt5",
                clean_mode=clean
            )

            # Rebuild index after rendering
            print("\n🔄 Rebuilding bar index...")
            self.index_manager.build_index(force_rebuild=True)

            print("\n✅ Bar rendering completed!")
            print("="*80 + "\n")

        except Exception as e:
            vLog.error(f"❌ Bar rendering failed: {e}")
            raise

    def cmd_help(self):
        """Show help"""
        print("""
📚 Bar Index CLI - Usage

Commands:
    rebuild             Rebuild bar index from parquet files
    status              Show bar index status and overview
    report              Generate detailed report (saved to framework/reports)
    render [--clean]    Render bars from tick data
                        --clean: Delete all existing bars before rendering
    help                Show this help

Examples:
    python python/cli/bar_index_cli.py rebuild
    python python/cli/bar_index_cli.py status
    python python/cli/bar_index_cli.py report
    python python/cli/bar_index_cli.py render
    python python/cli/bar_index_cli.py render --clean

Reports are saved to: ./framework/reports/bar_index_YYYYMMDD_HHMMSS.json

⚠️  Note: 'render --clean' deletes ALL bars before re-rendering (recommended until bar append is implemented)
""")


def main():
    """Main entry point"""
    if len(sys.argv) < 2:
        print("❌ Missing command. Use 'help' for usage.")
        sys.exit(1)

    cli = BarIndexCLI()
    command = sys.argv[1].lower()

    try:
        if command == "rebuild":
            cli.cmd_rebuild()

        elif command == "status":
            cli.cmd_status()

        elif command == "report":
            cli.cmd_report()

        elif command == "render":
            # Check for --clean flag
            clean = "--clean" in sys.argv
            cli.cmd_render(clean=clean)

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
