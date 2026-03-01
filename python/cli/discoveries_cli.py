"""
Discoveries CLI
Command-line interface for market discoveries, data analysis,
and unified cache management.

Commands:
- analyze: Analyze market data and show volatility/activity report
- extreme-moves: Scan for extreme directional price movements
- coverage: Gap analysis and coverage report management
- cache: Unified discovery cache operations
"""

import argparse
import sys
from typing import List, Optional

from python.framework.discoveries.data_coverage.data_coverage_report_cache import DataCoverageReportCache
from python.framework.discoveries.discovery_cache_manager import DiscoveryCacheManager
from python.framework.discoveries.market_analyzer import MarketAnalyzer
from python.framework.discoveries.discovery_cache import DiscoveryCache
from python.framework.discoveries.extreme_move_scanner import ExtremeMoveScanner
from python.framework.types.scenario_generator_types import (
    SymbolAnalysis,
)
from python.framework.reporting.market_report import print_analysis_report
from python.framework.reporting.comparison_report import print_cross_instrument_ranking
from python.framework.logging.bootstrap_logger import get_global_logger
from python.data_management.index.bars_index_manager import BarsIndexManager

vLog = get_global_logger()


class DiscoveriesCLI:
    """
    CLI handler for market discoveries and analysis.
    """

    def __init__(self):
        self._analyzer = MarketAnalyzer()

    # =========================================================================
    # ANALYZE COMMAND
    # =========================================================================

    def cmd_analyze(
        self,
        broker_type: str,
        symbol: str,
        timeframe: Optional[str] = None
    ) -> None:
        """
        Analyze market data and print report with cross-instrument comparison.

        Args:
            broker_type: Broker type identifier (e.g., 'mt5', 'kraken_spot')
            symbol: Symbol to analyze
            timeframe: Timeframe override
        """
        try:
            analysis = self._analyzer.analyze_symbol(
                broker_type, symbol, timeframe)
            print_analysis_report(analysis)
        except Exception as e:
            print(f"Failed to analyze {symbol}: {e}")
            vLog.error(f"Analysis failed for {symbol}: {e}")
            return

        all_symbols = self._analyzer.list_symbols(broker_type)
        all_analyses: List[SymbolAnalysis] = [analysis]

        for sym in all_symbols:
            if sym == symbol:
                continue
            try:
                sym_analysis = self._analyzer.analyze_symbol(
                    broker_type, sym, timeframe)
                all_analyses.append(sym_analysis)
            except Exception as e:
                vLog.warning(f"Could not analyze {sym} for comparison: {e}")

        if len(all_analyses) > 1:
            config = self._analyzer.get_config()
            top_count = config.cross_instrument_ranking.top_count
            print_cross_instrument_ranking(all_analyses, symbol, top_count)

    # =========================================================================
    # EXTREME MOVES COMMAND
    # =========================================================================

    def cmd_extreme_moves(
        self,
        broker_type: str,
        symbol: str,
        timeframe: Optional[str] = None,
        top_n: int = 10,
        force_rebuild: bool = False
    ) -> None:
        """
        Scan for extreme directional price movements (strong LONG/SHORT trends).
        Uses cache by default, rescans only when source bar data changes.

        Args:
            broker_type: Broker type identifier
            symbol: Symbol to scan
            timeframe: Timeframe override
            top_n: Number of top results to show
            force_rebuild: Force rescan ignoring cache
        """
        cache = DiscoveryCache()
        result = cache.get_extreme_moves(
            broker_type, symbol, force_rebuild=force_rebuild)

        if not result:
            print(f"No data available for {broker_type}/{symbol}")
            return

        scanner = ExtremeMoveScanner()
        scanner.print_result(result, top_n)

    # =========================================================================
    # COVERAGE COMMANDS
    # =========================================================================

    def cmd_data_coverage_build(self, force: bool = False) -> None:
        """
        Build coverage report cache for all symbols.

        Args:
            force: Force rebuild even if cache is valid
        """
        print("\n" + "="*80)
        print("🔧 Building Data Coverage Report Cache")
        print("="*80)
        print(
            f"Force Rebuild: {'ENABLED' if force else 'DISABLED (skip valid caches)'}")
        print("="*80 + "\n")

        bar_index = BarsIndexManager()
        bar_index.build_index()

        cache = DataCoverageReportCache()
        stats = cache.build_all(force_rebuild=force)

        print("\n" + "-"*60)
        print("📊 Build Summary:")
        print(f"   ✅ Generated: {stats['generated']}")
        print(f"   ⏭️  Skipped:   {stats['skipped']}")
        print(f"   ❌ Failed:    {stats['failed']}")
        print("-"*60 + "\n")

    def cmd_data_coverage_status(self) -> None:
        """Show coverage cache status overview."""
        bar_index = BarsIndexManager()
        bar_index.build_index()

        cache = DataCoverageReportCache()
        cache.print_status()

    def cmd_data_coverage_show(
        self, broker_type: str, symbol: str, force: bool = False
    ) -> None:
        """
        Show coverage report for a symbol.

        Args:
            broker_type: Broker type identifier
            symbol: Trading symbol
            force: Force regeneration (ignore cache)
        """
        bar_index = BarsIndexManager()
        bar_index.build_index()

        cache = DataCoverageReportCache()
        report = cache.get_report(broker_type, symbol, force_rebuild=force)

        if report is None:
            print(f"\n❌ No data available for {broker_type}/{symbol}\n")
            return

        print(report.generate_report())

    def cmd_data_coverage_validate(self) -> None:
        """Validate all symbols and show gap summary."""
        bar_index = BarsIndexManager()
        bar_index.build_index()

        cache = DataCoverageReportCache()

        print("\n" + "="*60)
        print("🔍 Validating All Symbols")
        print("="*60 + "\n")

        issues_found = False

        for broker_type in bar_index.list_broker_types():
            print(f"\n📂 {broker_type}:")

            for symbol in bar_index.list_symbols(broker_type):
                try:
                    report = cache.get_report(broker_type, symbol)

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
            print("Use 'coverage show BROKER_TYPE SYMBOL' for detailed gap analysis")
        else:
            print("✅ All symbols have clean coverage")
        print("="*60 + "\n")

    def cmd_data_coverage_clear(self) -> None:
        """Clear all cached coverage reports."""
        print("\n" + "="*60)
        print("🗑️  Clearing Data Coverage Report Cache")
        print("="*60 + "\n")

        cache = DataCoverageReportCache()
        count = cache.clear_cache()

        print(f"✅ Deleted {count} cache files\n")

    # =========================================================================
    # CACHE COMMANDS (unified)
    # =========================================================================

    def cmd_cache_rebuild_all(self, force: bool = False) -> None:
        """
        Rebuild all discovery caches (coverage + extreme moves).

        Args:
            force: Force rebuild even if cache is valid
        """
        bar_index = BarsIndexManager()
        bar_index.build_index()

        manager = DiscoveryCacheManager()
        results = manager.rebuild_all(force=force)

        print("\n" + "="*60)
        print("📊 Cache Rebuild Summary")
        print("="*60)
        for name, stats in results.items():
            print(f"\n  {name}:")
            print(f"    ✅ Generated: {stats['generated']}")
            print(f"    ⏭️  Skipped:   {stats['skipped']}")
            print(f"    ❌ Failed:    {stats['failed']}")
        print("\n" + "="*60 + "\n")

    def cmd_cache_status(self) -> None:
        """Show status of all discovery caches."""
        bar_index = BarsIndexManager()
        bar_index.build_index()

        manager = DiscoveryCacheManager()
        all_status = manager.status()

        print("\n" + "="*60)
        print("📊 Discovery Cache Status")
        print("="*60)

        for name, status in all_status.items():
            print(f"\n  {name}:")
            for key, value in status.items():
                print(f"    {key}: {value}")

        print("\n" + "="*60 + "\n")


# =============================================================================
# MAIN ENTRY POINT
# =============================================================================

def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Market discoveries, analysis, and cache management CLI",
        formatter_class=argparse.RawDescriptionHelpFormatter
    )

    subparsers = parser.add_subparsers(dest='command', help='Commands')

    # ─────────────────────────────────────────────────────────────────────────
    # ANALYZE command
    # ─────────────────────────────────────────────────────────────────────────
    analyze_parser = subparsers.add_parser(
        'analyze',
        help='Analyze market data for volatility and activity'
    )
    analyze_parser.add_argument(
        'broker_type',
        help='Broker type (e.g., mt5, kraken_spot)'
    )
    analyze_parser.add_argument(
        'symbol',
        help='Symbol to analyze (e.g., EURUSD, BTCUSD)'
    )
    analyze_parser.add_argument(
        '--timeframe',
        type=str,
        default=None,
        help='Timeframe to analyze (default: M5)'
    )

    # ─────────────────────────────────────────────────────────────────────────
    # EXTREME-MOVES command
    # ─────────────────────────────────────────────────────────────────────────
    extreme_parser = subparsers.add_parser(
        'extreme-moves',
        help='Scan for extreme directional price movements'
    )
    extreme_parser.add_argument(
        'broker_type',
        help='Broker type (e.g., mt5, kraken_spot)'
    )
    extreme_parser.add_argument(
        'symbol',
        help='Symbol to scan (e.g., EURUSD, BTCUSD)'
    )
    extreme_parser.add_argument(
        '--timeframe',
        type=str,
        default=None,
        help='Timeframe to analyze (default: M5)'
    )
    extreme_parser.add_argument(
        '--top',
        type=int,
        default=10,
        help='Number of top extreme moves to show (default: 10)'
    )
    extreme_parser.add_argument(
        '--force',
        action='store_true',
        default=False,
        help='Force rescan ignoring cache'
    )

    # ─────────────────────────────────────────────────────────────────────────
    # COVERAGE command group
    # ─────────────────────────────────────────────────────────────────────────
    coverage_parser = subparsers.add_parser(
        'data-coverage',
        help='Gap analysis and coverage report management'
    )
    coverage_sub = coverage_parser.add_subparsers(
        dest='coverage_command', help='Coverage commands')

    # coverage build
    cov_build = coverage_sub.add_parser(
        'build', help='Build coverage cache for all symbols')
    cov_build.add_argument(
        '--force', action='store_true', default=False,
        help='Force rebuild even if cache is valid')

    # coverage status
    coverage_sub.add_parser(
        'status', help='Show coverage cache status overview')

    # coverage show
    cov_show = coverage_sub.add_parser(
        'show', help='Show coverage report for a symbol')
    cov_show.add_argument(
        'broker_type', help='Broker type (e.g., mt5, kraken_spot)')
    cov_show.add_argument(
        'symbol', help='Symbol (e.g., EURUSD, BTCUSD)')
    cov_show.add_argument(
        '--force', action='store_true', default=False,
        help='Force regeneration (ignore cache)')

    # coverage validate
    coverage_sub.add_parser(
        'validate', help='Validate all symbols, show gap summary')

    # coverage clear
    coverage_sub.add_parser(
        'clear', help='Clear all cached coverage reports')

    # ─────────────────────────────────────────────────────────────────────────
    # CACHE command group (unified)
    # ─────────────────────────────────────────────────────────────────────────
    cache_parser = subparsers.add_parser(
        'cache',
        help='Unified discovery cache operations'
    )
    cache_sub = cache_parser.add_subparsers(
        dest='cache_command', help='Cache commands')

    # cache rebuild-all
    cache_rebuild = cache_sub.add_parser(
        'rebuild-all', help='Rebuild all discovery caches')
    cache_rebuild.add_argument(
        '--force', action='store_true', default=False,
        help='Force rebuild even if caches are valid')

    # cache status
    cache_sub.add_parser(
        'status', help='Show status of all discovery caches')

    # ─────────────────────────────────────────────────────────────────────────
    # Parse and execute
    # ─────────────────────────────────────────────────────────────────────────
    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        sys.exit(1)

    cli = DiscoveriesCLI()

    if args.command == 'analyze':
        cli.cmd_analyze(
            broker_type=args.broker_type,
            symbol=args.symbol,
            timeframe=args.timeframe
        )

    elif args.command == 'extreme-moves':
        cli.cmd_extreme_moves(
            broker_type=args.broker_type,
            symbol=args.symbol,
            timeframe=args.timeframe,
            top_n=args.top,
            force_rebuild=args.force
        )

    elif args.command == 'data-coverage':
        if not args.coverage_command:
            coverage_parser.print_help()
            sys.exit(1)

        if args.coverage_command == 'build':
            cli.cmd_data_coverage_build(force=args.force)
        elif args.coverage_command == 'status':
            cli.cmd_data_coverage_status()
        elif args.coverage_command == 'show':
            cli.cmd_data_coverage_show(
                broker_type=args.broker_type,
                symbol=args.symbol,
                force=args.force
            )
        elif args.coverage_command == 'validate':
            cli.cmd_data_coverage_validate()
        elif args.coverage_command == 'clear':
            cli.cmd_data_coverage_clear()

    elif args.command == 'cache':
        if not args.cache_command:
            cache_parser.print_help()
            sys.exit(1)

        if args.cache_command == 'rebuild-all':
            cli.cmd_cache_rebuild_all(force=args.force)
        elif args.cache_command == 'status':
            cli.cmd_cache_status()


if __name__ == '__main__':
    main()
