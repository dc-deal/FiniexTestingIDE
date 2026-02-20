"""
Discoveries CLI
===============
Command-line interface for market discoveries and data analysis.

Commands:
- analyze: Analyze market data and show volatility/activity report
- extreme-moves: Scan for extreme directional price movements

Location: python/cli/discoveries_cli.py
"""

import argparse
import sys
from typing import List, Optional

from python.framework.discoveries.market_analyzer import MarketAnalyzer
from python.framework.discoveries.discovery_cache import DiscoveryCache
from python.framework.discoveries.extreme_move_scanner import ExtremeMoveScanner
from python.framework.types.scenario_generator_types import (
    SymbolAnalysis,
)
from python.framework.reporting.market_report import print_analysis_report
from python.framework.reporting.comparison_report import print_cross_instrument_ranking
from python.framework.logging.bootstrap_logger import get_global_logger

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


# =============================================================================
# MAIN ENTRY POINT
# =============================================================================

def main():
    """Main CLI entry point."""
    parser = argparse.ArgumentParser(
        description="Market discoveries and data analysis CLI",
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


if __name__ == "__main__":
    main()
