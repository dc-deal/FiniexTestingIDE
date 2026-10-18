"""
FiniexTestingIDE - Spread Estimate CLI
The spread of a symbol's archive, estimated from its trades' aggressor sides

Usage:
    python python/cli/spread_estimate_cli.py show BROKER_TYPE SYMBOL
    python python/cli/spread_estimate_cli.py show BROKER_TYPE SYMBOL --year 2021
    python python/cli/spread_estimate_cli.py build [BROKER_TYPE SYMBOL] [--force]
"""

import argparse
import sys
from typing import Optional

from python.framework.discoveries.spread_estimate.spread_estimate_cache import (
    SpreadEstimateCache,
)
from python.framework.discoveries.spread_estimate.spread_estimate_measurement import (
    SpreadEstimateMeasurement,
)
from python.framework.discoveries.spread_estimate.spread_estimate_report import (
    print_spread_cache_build_all,
    print_spread_cache_built,
    print_spread_measurement,
)


class SpreadEstimateCli:
    """Command-line interface for the spread estimate."""

    def cmd_show(self, broker_type: str, symbol: str, year: Optional[int] = None) -> None:
        """
        Measure a symbol and print the yearly table, or one year's days.

        Args:
            broker_type: Broker type identifier
            symbol: Trading symbol
            year: Print this year's days instead of the yearly table
        """
        measurement = SpreadEstimateMeasurement().measure(broker_type, symbol)
        print_spread_measurement(measurement, year)

    def cmd_build(self, broker_type: Optional[str], symbol: Optional[str],
                  force: bool = False) -> None:
        """
        Build the day-estimate cache of one symbol, or of every symbol with a file without a quote.

        Args:
            broker_type: Broker type identifier; None with symbol None builds every symbol
            symbol: Trading symbol
            force: Measure again even where the cache is valid
        """
        cache = SpreadEstimateCache()
        if broker_type is None:
            print_spread_cache_build_all(cache.build_all(force_rebuild=force))
            return
        print_spread_cache_built(broker_type, symbol,
                                 cache.get_day_estimates(broker_type, symbol, force_rebuild=force))


def main():
    """Main entry point."""
    parser = argparse.ArgumentParser(
        description='The spread of a symbol, estimated from its trades',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    subparsers = parser.add_subparsers(dest='command', help='Commands')

    show_parser = subparsers.add_parser(
        'show', help='Measure a symbol: per year, or per day of one year')
    show_parser.add_argument('broker_type', help='Broker type identifier')
    show_parser.add_argument('symbol', help='Trading symbol')
    show_parser.add_argument('--year', type=int, default=None,
                             help="Print this year's days instead of the yearly table")

    build_parser = subparsers.add_parser(
        'build', help='Build the day-estimate cache a backtest prices its fills from')
    build_parser.add_argument('broker_type', nargs='?', default=None,
                              help='Broker type identifier (omit both to build every symbol)')
    build_parser.add_argument('symbol', nargs='?', default=None, help='Trading symbol')
    build_parser.add_argument('--force', action='store_true',
                              help='Measure again even where the cache is valid')

    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        sys.exit(1)

    cli = SpreadEstimateCli()
    if args.command == 'show':
        cli.cmd_show(args.broker_type, args.symbol, args.year)
    elif args.command == 'build':
        if (args.broker_type is None) != (args.symbol is None):
            parser.error('build takes a broker type AND a symbol, or neither')
        cli.cmd_build(args.broker_type, args.symbol, args.force)


if __name__ == '__main__':
    main()
