"""
FiniexTestingIDE - Bar Index CLI
Command-line tools for pre-rendered bar index management and reporting

Usage:
    python python/cli/bar_index_cli.py rebuild
    python python/cli/bar_index_cli.py status
    python python/cli/bar_index_cli.py report
    python python/cli/bar_index_cli.py render BROKER_TYPE [--clean]

REFACTORED: Index structure is now {broker_type: {symbol: {timeframe: entry}}}
"""

import sys
from pathlib import Path
from datetime import datetime
import traceback

from python.configuration.app_config_manager import AppConfigManager
from python.configuration.market_config_manager import MarketConfigManager
from python.data_management.index.bars_index_manager import BarsIndexManager
from python.framework.reporting.bar_index_report import BarIndexReportGenerator

from python.framework.logging.bootstrap_logger import get_global_logger
from python.framework.utils.activity_volume_provider import get_activity_provider
from python.data_management.importers.bar_importer import BarImporter

vLog = get_global_logger()


class BarIndexCLI:
    """
    Command-line interface for bar index management and reporting.
    """

    def __init__(self):
        """Initialize CLI with paths from AppConfigManager."""
        self.index_manager = BarsIndexManager()

    def cmd_rebuild(self):
        """Rebuild bar index from scratch"""
        print("\n" + "="*80)
        print("🔄 Rebuilding Bar Index")
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

        # Get broker_types first
        broker_types = self.index_manager.list_broker_types()
        print(
            f"Broker Types: {len(broker_types)} ({', '.join(broker_types) if broker_types else 'none'})")

        # Symbol overview (across all broker_types)
        all_symbols = self.index_manager.list_symbols()
        print(
            f"Symbols:     {len(all_symbols)} (total across all broker_types)")

        if not broker_types:
            print("\n⚠️  No bar data found in index")
            print("="*80 + "\n")
            return

        # Count total timeframes across all broker_types and symbols
        total_timeframes = 0
        for broker_type in broker_types:
            for symbol in self.index_manager.index[broker_type]:
                total_timeframes += len(
                    self.index_manager.index[broker_type][symbol])
        print(f"Timeframes:  {total_timeframes} (across all symbols)")

        # Iterate over broker_types → symbols → timeframes
        for broker_type in sorted(broker_types):
            print("\n" + "─"*80)
            print(f"📁 Broker Type: {broker_type}")
            print("─"*80)

            symbols = self.index_manager.list_symbols(broker_type)

            if not symbols:
                print("   (no symbols)")
                continue

            # List each symbol with available timeframes
            for symbol in sorted(symbols):
                # Pass broker_type to methods
                timeframes = self.index_manager.get_available_timeframes(
                    broker_type, symbol)
                stats = self.index_manager.get_symbol_stats(
                    broker_type, symbol)

                # Calculate total bars for this symbol
                total_bars = sum(tf_stats['bar_count']
                                 for tf_stats in stats.values())

                print(f"\n   {symbol}:")
                print(f"      Timeframes: {', '.join(timeframes)}")
                print(f"      Total bars: {total_bars:,}")

                # Get first timeframe entry for metadata
                first_tf = sorted(timeframes)[0]
                # Access with broker_type first
                first_entry = self.index_manager.index[broker_type][symbol][first_tf]

                # Get market_type from MarketConfigManager (Single Source of Truth)
                market_config = MarketConfigManager()
                market_type = market_config.get_market_type(broker_type).value

                # Version metadata from index entry
                source_version_min = first_entry.get(
                    'source_version_min', 'unknown')
                source_version_max = first_entry.get(
                    'source_version_max', 'unknown')
                # CHANGED: data_source → broker_type
                data_source = first_entry.get('broker_type', broker_type)

                # Version display
                if source_version_min == source_version_max:
                    version_str = source_version_min
                else:
                    version_str = f"{source_version_min} - {source_version_max}"

                print(f"      Source:     {data_source} (v{version_str})")
                print(f"      Market:     {market_type}")

                # Activity metrics using provider
                provider = get_activity_provider()
                activity_label = provider.get_metric_label(market_type)

                for tf in sorted(timeframes):
                    tf_stats = stats[tf]

                    # Get full entry for activity data
                    # Access with broker_type first
                    entry = self.index_manager.index[broker_type][symbol][tf]
                    total_activity = provider.get_total_activity_value(
                        entry, market_type)
                    avg_activity = provider.get_avg_activity_value(
                        entry, market_type)

                    print(f"         • {tf}: {tf_stats['bar_count']:,} bars "
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

    def cmd_render(self, broker_type: str, clean: bool = False):
        """
        Render bars from tick data.

        Args:
            broker_type: Broker type identifier (REQUIRED)
            clean: If True, delete existing bars before rendering
        """
        print("\n" + "="*80)
        print(f"🔄 Bar Rendering (broker_type: {broker_type})")
        print("="*80)
        print(
            f"Clean Mode: {'ENABLED (delete all bars first)' if clean else 'DISABLED (skip symbols without ticks)'}")
        print("="*80 + "\n")

        try:
            bar_importer = BarImporter()
            bar_importer.render_bars_for_all_symbols(
                broker_type=broker_type,
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
    rebuild                         Rebuild bar index from parquet files
    status                          Show bar index status and overview
    report                          Generate detailed report (saved to framework/reports)
    render BROKER_TYPE [--clean]    Render bars from tick data for specific broker_type
                                    BROKER_TYPE is REQUIRED (e.g., mt5, kraken_spot)
                                    --clean: Delete all existing bars before rendering
    help                            Show this help

Examples:
    python python/cli/bar_index_cli.py rebuild
    python python/cli/bar_index_cli.py status
    python python/cli/bar_index_cli.py report
    python python/cli/bar_index_cli.py render mt5
    python python/cli/bar_index_cli.py render kraken_spot --clean

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
            # render requires BROKER_TYPE as second argument
            if len(sys.argv) < 3:
                print("❌ Usage: render BROKER_TYPE [--clean]")
                print("   Example: render mt5")
                print("   Example: render kraken_spot --clean")
                sys.exit(1)

            broker_type = sys.argv[2]
            clean = "--clean" in sys.argv

            cli.cmd_render(broker_type=broker_type, clean=clean)

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
