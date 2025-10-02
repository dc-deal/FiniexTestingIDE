"""
FiniexTestingIDE Data Loader - Reports Module
Summary reports, formatted output, and developer convenience tools

Location: python/data_worker/reports.py
"""

import logging
from typing import Dict

from python.data_worker.data_loader.analytics import TickDataAnalyzer
from python.data_worker.data_loader.core import TickDataLoader
from python.components.logger.bootstrap_logger import setup_logging

setup_logging(name="StrategyRunner")
logger = logging.getLogger(__name__)


class TickDataReporter:
    """Generates formatted reports and summaries for tick data"""

    def __init__(self, loader: TickDataLoader, analyzer: TickDataAnalyzer):
        """Initialize reporter with loader and analyzer"""
        self.loader = loader
        self.analyzer = analyzer

    def print_symbol_info(self, info: Dict):
        """logger.info formatted symbol information to console"""
        if "error" in info:
            logger.info(
                f"\n❌ {info.get('symbol', 'UNKNOWN')}: {info['error']}")
            return

        weekends = info["date_range"]["duration"]["weekends"]

        logger.info(f"\n📊 {info['symbol']}")
        logger.info(
            f"   ├─ Time Range:    {info['date_range']['start_formatted']} to "
            f"{info['date_range']['end_formatted']}"
        )
        logger.info(
            f"   ├─ Duration:      {info['date_range']['duration']['days']} days "
            f"({info['date_range']['duration']['hours']:.1f} hours)"
        )
        logger.info(
            f"   ├─ Trading Days:  {info['date_range']['duration']['trading_days']} "
            f"(excluding {weekends['full_weekends']} weekends)"
        )
        logger.info(
            f"   │  └─ Weekends:   {weekends['full_weekends']}x complete "
            f"({weekends['saturdays']} Sat, {weekends['sundays']} Sun)"
        )
        logger.info(f"   ├─ Ticks:         {info['total_ticks']:,}")
        logger.info(f"   ├─ Files:         {info['files']}")
        logger.info(f"   ├─ Size:          {info['file_size_mb']:.1f} MB")

        if info["statistics"]["avg_spread_points"]:
            logger.info(
                f"   ├─ Ø Spread:      {info['statistics']['avg_spread_points']:.1f} Points "
                f"({info['statistics']['avg_spread_pct']:.4f}%)"
            )

        logger.info(
            f"   └─ Frequency:     {info['statistics']['tick_frequency_per_second']:.2f} "
            f"Ticks/Second"
        )

        if info.get("sessions"):
            sessions_str = ", ".join(
                [f"{k}: {v}" for k, v in info["sessions"].items()])
            logger.info(f"      Sessions:     {sessions_str}")

    def print_all_symbols(self):
        """logger.info summary for all available symbols"""
        symbols = self.loader.list_available_symbols()

        logger.info("\n" + "=" * 100)
        logger.info("SYMBOL OVERVIEW WITH TIME RANGES")
        logger.info("=" * 100)

        for symbol in symbols:
            info = self.analyzer.get_symbol_info(symbol)
            self.print_symbol_info(info)

        logger.info("\n" + "=" * 100)

    def test_load_symbol(self, symbol: str):
        """Test loading data for a symbol and display sample"""
        logger.info(f"\n🧪 TEST LOAD: {symbol}")
        logger.info("=" * 100)

        info = self.analyzer.get_symbol_info(symbol)

        if "error" in info:
            logger.info(f"❌ Cannot load {symbol}: {info['error']}")
            return

        df = self.loader.load_symbol_data(
            symbol,
            start_date=info["date_range"]["start_formatted"].split()[0],
            end_date=info["date_range"]["end_formatted"].split()[0],
        )

        logger.info(f"✓ Loaded:      {len(df):,} ticks")
        logger.info(
            f"✓ Time Range:  {df['timestamp'].min()} to {df['timestamp'].max()}")
        logger.info(
            f"✓ Columns:     {', '.join(df.columns[:5])}... ({len(df.columns)} total)"
        )
        logger.info(f"\n📋 Sample Data (first 3 ticks):")
        logger.info(df.head(3).to_string())


def run_summary_report():
    """
    Run comprehensive summary report for all available data

    Main entry point for developers to inspect their data.
    """
    logger.info("=== FiniexTestingIDE Data Loader Summary Report ===")

    try:
        # Initialize components
        loader = TickDataLoader()
        analyzer = TickDataAnalyzer(loader)
        reporter = TickDataReporter(loader, analyzer)

        # Check if data exists
        symbols = loader.list_available_symbols()
        logger.info(f"Available symbols: {symbols}")

        if not symbols:
            logger.error("❌ No data found!")
            logger.info("\n" + "=" * 100)
            logger.info("NO DATA FOUND")
            logger.info("=" * 100)
            logger.info("\nSteps to collect data:")
            logger.info("1. Copy TickCollector.mq5 to MetaTrader 5")
            logger.info("2. Run data collection for 48+ hours")
            logger.info("3. Execute: python python/tick_importer.py")
            logger.info("4. Run this report again")
            return

        # logger.info all symbols
        reporter.print_all_symbols()

        # Test load first symbol
        if symbols:
            reporter.test_load_symbol(symbols[0])

        logger.info("\n✅ Summary report completed successfully!")

    except Exception as e:
        logger.error(f"Error generating report: {e}", exc_info=True)


def main():
    """Main entry point for command-line execution"""
    run_summary_report()


if __name__ == "__main__":
    main()
