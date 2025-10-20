"""
FiniexTestingIDE Data Loader - Reports Module
Summary reports, formatted output, and developer convenience tools

Location: python/data_worker/reports.py
"""

import traceback

import pandas as pd
from typing import Dict

from python.data_worker.data_loader.analytics import TickDataAnalyzer
from python.data_worker.data_loader.core import TickDataLoader

from python.components.logger.bootstrap_logger import get_logger
vLog = get_logger()


class TickDataReporter:
    """Generates formatted reports and summaries for tick data"""

    def __init__(self, loader: TickDataLoader, analyzer: TickDataAnalyzer):
        """Initialize reporter with loader and analyzer"""
        self.loader = loader
        self.analyzer = analyzer

    def print_symbol_info(self, info: Dict):
        """vLog.info formatted symbol information to console"""
        if "error" in info:
            vLog.info(
                f"\n❌ {info.get('symbol', 'UNKNOWN')}: {info['error']}")
            return

        weekends = info["date_range"]["duration"]["weekends"]

        vLog.info(f"\n📊 {info['symbol']}")
        vLog.info(
            f"   ├─ Time Range:    {info['date_range']['start_formatted']} to "
            f"{info['date_range']['end_formatted']}"
        )
        vLog.info(
            f"   ├─ Duration:      {info['date_range']['duration']['days']} days "
            f"({info['date_range']['duration']['hours']:.1f} hours)"
        )
        vLog.info(
            f"   ├─ Trading Days:  {info['date_range']['duration']['trading_days']} "
            f"(excluding {weekends['full_weekends']} weekends)"
        )
        vLog.info(
            f"   │  └─ Weekends:   {weekends['full_weekends']}x complete "
            f"({weekends['saturdays']} Sat, {weekends['sundays']} Sun)"
        )
        vLog.info(f"   ├─ Ticks:         {info['total_ticks']:,}")
        vLog.info(f"   ├─ Files:         {info['files']}")
        vLog.info(f"   ├─ Size:          {info['file_size_mb']:.1f} MB")

        if info["statistics"]["avg_spread_points"]:
            vLog.info(
                f"   ├─ Ø Spread:      {info['statistics']['avg_spread_points']:.1f} Points "
                f"({info['statistics']['avg_spread_pct']:.4f}%)"
            )

        vLog.info(
            f"   └─ Frequency:     {info['statistics']['tick_frequency_per_second']:.2f} "
            f"Ticks/Second"
        )

        if info.get("sessions"):
            sessions_str = ", ".join(
                [f"{k}: {v}" for k, v in info["sessions"].items()])
            vLog.info(f"      Sessions:     {sessions_str}")

    def print_all_symbols(self):
        """vLog.info summary for all available symbols"""
        symbols = self.loader.list_available_symbols()

        vLog.info("\n" + "=" * 100)
        vLog.info("SYMBOL OVERVIEW WITH TIME RANGES")
        vLog.info("=" * 100)

        for symbol in symbols:
            info = self.analyzer.get_symbol_info(symbol)
            self.print_symbol_info(info)

        vLog.info("\n" + "=" * 100)

    def test_load_symbol(self, symbol: str):
        """Test loading data for a symbol and display sample"""
        vLog.info(f"\n🧪 TEST LOAD: {symbol}")
        vLog.info("=" * 100)

        info = self.analyzer.get_symbol_info(symbol)

        if "error" in info:
            vLog.info(f"❌ Cannot load {symbol}: {info['error']}")
            return

        start_date = info["date_range"]["start_formatted"].split()[
            0]
        end_date = info["date_range"]["end_formatted"].split()[
            0]
        start_dt = pd.to_datetime(start_date).tz_localize('UTC')
        end_dt = pd.to_datetime(end_date).tz_localize('UTC')

        df = self.loader.load_symbol_data(
            symbol,
            start_date=start_dt,
            end_date=end_dt,
        )

        vLog.info(f"✓ Loaded:      {len(df):,} ticks")
        vLog.info(
            f"✓ Time Range:  {df['timestamp'].min()} to {df['timestamp'].max()}")
        vLog.info(
            f"✓ Columns:     {', '.join(df.columns[:5])}... ({len(df.columns)} total)"
        )
        vLog.info(f"\n📋 Sample Data (first 3 ticks):")
        vLog.info(df.head(3).to_string())


def run_summary_report():
    """
    Run comprehensive summary report for all available data

    Main entry point for developers to inspect their data.
    """
    vLog.info("=== FiniexTestingIDE Data Loader Summary Report ===")

    try:
        # Initialize components
        loader = TickDataLoader()
        analyzer = TickDataAnalyzer(loader)
        reporter = TickDataReporter(loader, analyzer)

        # Check if data exists
        symbols = loader.list_available_symbols()
        vLog.info(f"Available symbols: {symbols}")

        if not symbols:
            vLog.error("❌ No data found!")
            vLog.info("\n" + "=" * 100)
            vLog.info("NO DATA FOUND")
            vLog.info("=" * 100)
            vLog.info("\nSteps to collect data:")
            vLog.info("1. Copy TickCollector.mq5 to MetaTrader 5")
            vLog.info("2. Run data collection for 48+ hours")
            vLog.info("3. Execute: python python/tick_importer.py")
            vLog.info("4. Run this report again")
            return

        # vLog.info all symbols
        reporter.print_all_symbols()

        # Test load first symbol
        if symbols:
            reporter.test_load_symbol(symbols[0])

        vLog.info("\n✅ Summary report completed successfully!")

    except Exception as e:
        vLog.error(f"Error generating report: \n{traceback.format_exc()}")


def main():
    """Main entry point for command-line execution"""
    run_summary_report()


if __name__ == "__main__":
    main()
