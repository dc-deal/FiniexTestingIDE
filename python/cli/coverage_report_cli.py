"""
FiniexTestingIDE - Coverage Report CLI
Command-line tools for gap analysis and coverage report management

Usage:
    python python/cli/coverage_report_cli.py build [--force]
    python python/cli/coverage_report_cli.py status
    python python/cli/coverage_report_cli.py show BROKER_TYPE SYMBOL
    python python/cli/coverage_report_cli.py validate
    python python/cli/coverage_report_cli.py clear

REFACTORED: Extracted from data_index_cli.py for separation of concerns
"""

import sys
import traceback
from pathlib import Path

from python.framework.reporting.coverage_report_cache import CoverageReportCache
from python.data_management.index.bars_index_manager import BarsIndexManager
from python.framework.logging.bootstrap_logger import get_global_logger

vLog = get_global_logger()


class CoverageReportCLI:
    """
    Command-line interface for coverage report management.

    Manages gap analysis cache and provides report viewing.
    """

    def __init__(self):
        """Initialize CLI."""
        self._cache = CoverageReportCache()
        self._bar_index = BarsIndexManager()

    def cmd_build(self, force: bool = False):
        """
        Build coverage report cache for all symbols.

        Args:
            force: Force rebuild even if cache is valid
        """
        print("\n" + "="*80)
        print("🔧 Building Coverage Report Cache")
        print("="*80)
        print(
            f"Force Rebuild: {'ENABLED' if force else 'DISABLED (skip valid caches)'}")
        print("="*80 + "\n")

        # Ensure bar index is loaded
        self._bar_index.build_index()

        stats = self._cache.build_all(force_rebuild=force)

        print("\n" + "-"*60)
        print("📊 Build Summary:")
        print(f"   ✅ Generated: {stats['generated']}")
        print(f"   ⏭️  Skipped:   {stats['skipped']}")
        print(f"   ❌ Failed:    {stats['failed']}")
        print("-"*60 + "\n")

    def cmd_status(self):
        """Show cache status overview."""
        # Ensure bar index is loaded
        self._bar_index.build_index()

        self._cache.print_status()

    def cmd_show(self, broker_type: str, symbol: str, force: bool = False):
        """
        Show coverage report for a symbol.

        Args:
            broker_type: Broker type identifier
            symbol: Trading symbol
            force: Force regeneration (ignore cache)
        """
        # Ensure bar index is loaded
        self._bar_index.build_index()

        report = self._cache.get_report(
            broker_type, symbol, force_rebuild=force)

        if report is None:
            print(f"\n❌ No data available for {broker_type}/{symbol}\n")
            return

        print(report.generate_report())

    def cmd_validate(self):
        """Validate all symbols and show gap summary."""
        # Ensure bar index is loaded
        self._bar_index.build_index()

        print("\n" + "="*60)
        print("🔍 Validating All Symbols")
        print("="*60 + "\n")

        issues_found = False

        for broker_type in self._bar_index.list_broker_types():
            print(f"\n📂 {broker_type}:")

            for symbol in self._bar_index.list_symbols(broker_type):
                try:
                    report = self._cache.get_report(broker_type, symbol)

                    if report is None:
                        print(f"  ❌ {symbol}: No data available")
                        issues_found = True
                        continue

                    if report.has_issues():
                        moderate = report.gap_counts.get('moderate', 0)
                        large = report.gap_counts.get('large', 0)
                        print(
                            f"  ⚠️  {symbol}: {moderate} moderate, {large} large gaps")
                        issues_found = True
                    else:
                        print(f"  ✅ {symbol}: No issues")

                except Exception as e:
                    print(f"  ❌ {symbol}: Error - {e}")
                    issues_found = True

        print("\n" + "="*60)
        if issues_found:
            print("Use 'show BROKER_TYPE SYMBOL' for detailed gap analysis")
        else:
            print("✅ All symbols have clean coverage")
        print("="*60 + "\n")

    def cmd_clear(self):
        """Clear all cached reports."""
        print("\n" + "="*60)
        print("🗑️  Clearing Coverage Report Cache")
        print("="*60 + "\n")

        count = self._cache.clear_cache()

        print(f"✅ Deleted {count} cache files\n")

    def cmd_help(self):
        """Show help."""
        print("""
📊 Coverage Report CLI - Usage

Commands:
    build [--force]              Build coverage cache for all symbols
                                 --force: Rebuild even if cache is valid
    
    status                       Show cache status overview
    
    show BROKER_TYPE SYMBOL      Show coverage report for symbol
         [--force]               --force: Regenerate (ignore cache)
    
    validate                     Validate all symbols, show gap summary
    
    clear                        Clear all cached reports
    
    help                         Show this help

Examples:
    python python/cli/coverage_report_cli.py build
    python python/cli/coverage_report_cli.py build --force
    python python/cli/coverage_report_cli.py status
    python python/cli/coverage_report_cli.py show mt5 EURUSD
    python python/cli/coverage_report_cli.py show kraken_spot BTCUSD --force
    python python/cli/coverage_report_cli.py validate
    python python/cli/coverage_report_cli.py clear

Cache Location: data/processed/.coverage_cache/
""")


def main():
    """Main entry point."""
    if len(sys.argv) < 2:
        print("❌ Missing command. Use 'help' for usage.")
        sys.exit(1)

    cli = CoverageReportCLI()
    command = sys.argv[1].lower()

    try:
        if command == "build":
            force = "--force" in sys.argv
            cli.cmd_build(force=force)

        elif command == "status":
            cli.cmd_status()

        elif command == "show":
            if len(sys.argv) < 4:
                print("❌ Usage: show BROKER_TYPE SYMBOL [--force]")
                print("   Example: show mt5 EURUSD")
                sys.exit(1)

            broker_type = sys.argv[2]
            symbol = sys.argv[3]
            force = "--force" in sys.argv

            cli.cmd_show(broker_type, symbol, force=force)

        elif command == "validate":
            cli.cmd_validate()

        elif command == "clear":
            cli.cmd_clear()

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
