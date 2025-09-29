"""
FiniexTestingIDE Data Loader - Reports Module
Summary reports, formatted output, and developer convenience tools

Location: python/data_loader/reports.py
"""

from typing import Dict
import logging

from python.data_loader.core import TickDataLoader
from python.data_loader.analytics import TickDataAnalyzer

logger = logging.getLogger(__name__)


class TickDataReporter:
    """Generates formatted reports and summaries for tick data"""

    def __init__(self, loader: TickDataLoader, analyzer: TickDataAnalyzer):
        """Initialize reporter with loader and analyzer"""
        self.loader = loader
        self.analyzer = analyzer

    def print_symbol_info(self, info: Dict):
        """Print formatted symbol information to console"""
        if "error" in info:
            print(f"\n❌ {info.get('symbol', 'UNKNOWN')}: {info['error']}")
            return

        weekends = info["date_range"]["duration"]["weekends"]

        print(f"\n📊 {info['symbol']}")
        print(
            f"   ├─ Time Range:    {info['date_range']['start_formatted']} to "
            f"{info['date_range']['end_formatted']}"
        )
        print(
            f"   ├─ Duration:      {info['date_range']['duration']['days']} days "
            f"({info['date_range']['duration']['hours']:.1f} hours)"
        )
        print(
            f"   ├─ Trading Days:  {info['date_range']['duration']['trading_days']} "
            f"(excluding {weekends['full_weekends']} weekends)"
        )
        print(
            f"   │  └─ Weekends:   {weekends['full_weekends']}x complete "
            f"({weekends['saturdays']} Sat, {weekends['sundays']} Sun)"
        )
        print(f"   ├─ Ticks:         {info['total_ticks']:,}")
        print(f"   ├─ Files:         {info['files']}")
        print(f"   ├─ Size:          {info['file_size_mb']:.1f} MB")

        if info["statistics"]["avg_spread_points"]:
            print(
                f"   ├─ Ø Spread:      {info['statistics']['avg_spread_points']:.1f} Points "
                f"({info['statistics']['avg_spread_pct']:.4f}%)"
            )

        print(
            f"   └─ Frequency:     {info['statistics']['tick_frequency_per_second']:.2f} "
            f"Ticks/Second"
        )

        if info.get("sessions"):
            sessions_str = ", ".join([f"{k}: {v}" for k, v in info["sessions"].items()])
            print(f"      Sessions:     {sessions_str}")

    def print_all_symbols(self):
        """Print summary for all available symbols"""
        symbols = self.loader.list_available_symbols()

        print("\n" + "=" * 100)
        print("SYMBOL OVERVIEW WITH TIME RANGES")
        print("=" * 100)

        for symbol in symbols:
            info = self.analyzer.get_symbol_info(symbol)
            self.print_symbol_info(info)

        print("\n" + "=" * 100)

    def test_load_symbol(self, symbol: str):
        """Test loading data for a symbol and display sample"""
        print(f"\n🧪 TEST LOAD: {symbol}")
        print("=" * 100)

        info = self.analyzer.get_symbol_info(symbol)

        if "error" in info:
            print(f"❌ Cannot load {symbol}: {info['error']}")
            return

        df = self.loader.load_symbol_data(
            symbol,
            start_date=info["date_range"]["start_formatted"].split()[0],
            end_date=info["date_range"]["end_formatted"].split()[0],
        )

        print(f"✓ Loaded:      {len(df):,} ticks")
        print(f"✓ Time Range:  {df['timestamp'].min()} to {df['timestamp'].max()}")
        print(
            f"✓ Columns:     {', '.join(df.columns[:5])}... ({len(df.columns)} total)"
        )
        print(f"\n📋 Sample Data (first 3 ticks):")
        print(df.head(3).to_string())


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
            print("\n" + "=" * 100)
            print("NO DATA FOUND")
            print("=" * 100)
            print("\nSteps to collect data:")
            print("1. Copy TickCollector.mq5 to MetaTrader 5")
            print("2. Run data collection for 48+ hours")
            print("3. Execute: python python/tick_importer.py")
            print("4. Run this report again")
            return

        # Print all symbols
        reporter.print_all_symbols()

        # Test load first symbol
        if symbols:
            reporter.test_load_symbol(symbols[0])

        print("\n✅ Summary report completed successfully!")

    except Exception as e:
        logger.error(f"Error generating report: {e}", exc_info=True)


def main():
    """Main entry point for command-line execution"""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s - %(levelname)s - %(message)s"
    )
    run_summary_report()


if __name__ == "__main__":
    main()
