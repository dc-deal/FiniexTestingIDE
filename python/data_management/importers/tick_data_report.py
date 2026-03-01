"""
FiniexTestingIDE Data Loader - Reports Module
Summary reports, formatted output, and developer convenience tools

"""

from pathlib import Path
import traceback

import pandas as pd
from typing import Dict

from python.configuration.app_config_manager import AppConfigManager
from python.framework.logging.bootstrap_logger import get_global_logger
from python.data_management.index.tick_index_manager import TickIndexManager
from python.framework.utils.market_calendar import MarketCalendar
vLog = get_global_logger()


class TickDataReporter:
    """Generates formatted reports and summaries for tick data"""

    def __init__(self, index_manager: TickIndexManager):
        """
        Initialize reporter with index manager.

        Args:
            index_manager: TickIndexManager instance
        """
        self.index_manager = index_manager

    def get_symbol_info(self, broker_type: str, symbol: str) -> Dict:
        """
        Get comprehensive information about a symbol from index.

        Args:
            broker_type: Broker type identifier (e.g., 'mt5', 'kraken_spot')
            symbol: Trading symbol

        Returns:
            Dict with symbol information and statistics
        """
        # Check broker_type exists
        if broker_type not in self.index_manager.index:
            return {"error": f"No data found for broker_type {broker_type}"}

        # Check symbol exists for this broker_type
        if symbol not in self.index_manager.index[broker_type]:
            return {"error": f"No data found for symbol {symbol} in {broker_type}"}

        files = self.index_manager.index[broker_type][symbol]

        # === AGGREGATE STATISTICS FROM INDEX ===
        total_ticks = sum(f['tick_count'] for f in files)
        total_size_mb = sum(f['file_size_mb'] for f in files)

        # Time range
        start_time = pd.to_datetime(files[0]['start_time'])
        end_time = pd.to_datetime(files[-1]['end_time'])
        duration = end_time - start_time

        # Duration calculations
        duration_days = duration.days
        duration_hours = duration.total_seconds() / 3600
        duration_minutes = duration.total_seconds() / 60

        # Weekend analysis
        weekend_info = MarketCalendar.get_weekend_statistics(
            start_time, end_time)
        trading_days = duration_days - (weekend_info["full_weekends"] * 2)

        # === AGGREGATE SPREAD STATISTICS ===
        # Weighted average by tick_count
        total_weighted_spread_points = 0
        total_weighted_spread_pct = 0

        for f in files:
            if f.get('statistics') and f['statistics'].get('avg_spread_points'):
                total_weighted_spread_points += f['statistics']['avg_spread_points'] * \
                    f['tick_count']
                total_weighted_spread_pct += f['statistics']['avg_spread_pct'] * \
                    f['tick_count']

        avg_spread_points = round(
            total_weighted_spread_points / total_ticks, 2) if total_ticks > 0 else None
        avg_spread_pct = round(total_weighted_spread_pct /
                               total_ticks, 6) if total_ticks > 0 else None

        # === AGGREGATE SESSIONS ===
        sessions = {}
        for f in files:
            for session, count in f.get('sessions', {}).items():
                sessions[session] = sessions.get(session, 0) + count

        # === CALCULATE TICK FREQUENCY ===
        tick_frequency = round(
            total_ticks / duration.total_seconds(), 2) if duration.total_seconds() > 0 else 0.0

        # === MARKET METADATA (from first file) ===
        market_type = files[0].get('market_type', 'forex_cfd')
        data_source = files[0].get('broker_type', broker_type)

        return {
            "symbol": symbol,
            "broker_type": broker_type,
            "files": len(files),
            "total_ticks": total_ticks,
            "date_range": {
                "start": start_time.isoformat(),
                "end": end_time.isoformat(),
                "start_formatted": start_time.strftime("%Y-%m-%d %H:%M:%S"),
                "end_formatted": end_time.strftime("%Y-%m-%d %H:%M:%S"),
                "duration": {
                    "days": duration_days,
                    "hours": round(duration_hours, 2),
                    "minutes": round(duration_minutes, 2),
                    "total_seconds": duration.total_seconds(),
                    "trading_days": trading_days,
                    "weekends": weekend_info,
                },
            },
            "statistics": {
                "avg_spread_points": avg_spread_points,
                "avg_spread_pct": avg_spread_pct,
                "tick_frequency_per_second": tick_frequency,
            },
            "file_size_mb": round(total_size_mb, 2),
            "sessions": sessions,
            "market_type": market_type,
            "data_source": data_source,
        }

    def print_symbol_info(self, info: Dict):
        """Print formatted symbol information to console"""
        if "error" in info:
            vLog.info(
                f"\n❌ {info.get('symbol', 'UNKNOWN')}: {info['error']}")
            return

        weekends = info["date_range"]["duration"]["weekends"]

        vLog.info(f"\n📊 {info.get('broker_type', 'unknown')}/{info['symbol']}")
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

        if info.get("market_type"):
            vLog.info(f"   ├─ Market Type:  {info['market_type']}")

        if info.get("data_source"):
            vLog.info(f"   └─ Data Source:  {info['data_source']}")

    def print_all_symbols(self, broker_types: list = None):
        """
        Print summary for symbols.

        Args:
            broker_types: List of broker_types to show. If None, shows all.
        """
        if broker_types is None:
            broker_types = self.index_manager.list_broker_types()

        vLog.info("\n" + "=" * 100)
        vLog.info("SYMBOL OVERVIEW WITH TIME RANGES")
        vLog.info("=" * 100)

        for broker_type in broker_types:
            vLog.info(f"\n{'─' * 100}")
            vLog.info(f"📁 Broker Type: {broker_type}")
            vLog.info("─" * 100)

            symbols = self.index_manager.list_symbols(broker_type)

            for symbol in symbols:
                info = self.get_symbol_info(broker_type, symbol)
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

        vLog.info(f"✅ Loaded:      {len(df):,} ticks")
        vLog.info(
            f"✅ Time Range:  {df['timestamp'].min()} to {df['timestamp'].max()}")
        vLog.info(
            f"✅ Columns:     {', '.join(df.columns[:5])}... ({len(df.columns)} total)"
        )
        vLog.info(f"\n📋 Sample Data (first 3 ticks):")
        vLog.info(df.head(3).to_string())


def run_summary_report(broker_type: str = None):
    """
    Run comprehensive summary report for available data.

    Args:
        broker_type: Optional filter. If None, shows all broker_types.
    """
    vLog.info("=== FiniexTestingIDE Data Loader Summary Report ===")

    try:
        # Initialize index manager with path from AppConfigManager
        index_manager = TickIndexManager()
        index_manager.build_index()

        # Initialize reporter
        reporter = TickDataReporter(index_manager)

        # Check if data exists
        all_broker_types = index_manager.list_broker_types()

        if not all_broker_types:
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

        # Filter if broker_type specified
        if broker_type:
            if broker_type not in all_broker_types:
                vLog.error(f"❌ broker_type '{broker_type}' not found!")
                vLog.info(f"   Available: {', '.join(all_broker_types)}")
                return
            broker_types = [broker_type]
        else:
            broker_types = all_broker_types

        # Show overview
        all_symbols = []
        for bt in broker_types:
            symbols = index_manager.list_symbols(bt)
            all_symbols.extend([f"{bt}/{s}" for s in symbols])
        vLog.info(f"Broker types: {broker_types}")
        vLog.info(f"Symbols: {all_symbols}")

        # Print symbols (filtered or all)
        reporter.print_all_symbols(broker_types)

        vLog.info("\n✅ Summary report completed successfully!")

    except Exception as e:
        vLog.error(f"Error generating report: \n{traceback.format_exc()}")
