"""
FiniexTestingIDE - Spread Estimate CLI
The spread of a symbol's archive, estimated from its trades' aggressor sides

Usage:
    python python/cli/spread_estimate_cli.py show BROKER_TYPE SYMBOL
    python python/cli/spread_estimate_cli.py show BROKER_TYPE SYMBOL --year 2021
"""

import argparse
import sys
from typing import Optional

from python.framework.discoveries.spread_estimate.spread_estimate_measurement import (
    SpreadEstimateMeasurement,
)
from python.framework.discoveries.spread_estimate.spread_estimate_report import (
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

    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        sys.exit(1)

    cli = SpreadEstimateCli()
    if args.command == 'show':
        cli.cmd_show(args.broker_type, args.symbol, args.year)


if __name__ == '__main__':
    main()
