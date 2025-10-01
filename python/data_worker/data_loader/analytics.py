"""
FiniexTestingIDE Data Loader - Analytics Module
Symbol analysis, metadata extraction, and statistics

Location: python/data_worker/analytics.py
"""

import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Tuple

import pandas as pd
import pyarrow.parquet as pq

from python.data_worker.data_loader.core import TickDataLoader

logger = logging.getLogger(__name__)


class TickDataAnalyzer:
    """Provides analysis and statistics for tick data"""

    def __init__(self, loader: TickDataLoader):
        """Initialize analyzer with a data loader"""
        self.loader = loader

    def get_symbol_info(self, symbol: str) -> Dict[str, any]:
        """
        Get comprehensive information about a symbol

        Returns dict with: symbol, files, total_ticks, date_range,
        statistics, file_size_mb, sessions
        """
        files = self.loader._get_symbol_files(symbol)

        if not files:
            return {"error": f"No data found for symbol {symbol}"}

        try:
            # Load all files for statistics
            dataframes = []
            total_size = 0

            for file in files:
                df = pd.read_parquet(file)
                dataframes.append(df)
                total_size += file.stat().st_size

            # Combine data
            combined_df = pd.concat(dataframes, ignore_index=True)
            combined_df = combined_df.sort_values("timestamp")

            # Calculate time range
            start_time = combined_df["timestamp"].min()
            end_time = combined_df["timestamp"].max()
            duration = end_time - start_time

            # Calculate durations
            duration_days = duration.days
            duration_hours = duration.total_seconds() / 3600
            duration_minutes = duration.total_seconds() / 60

            # Weekend analysis
            weekend_info = self._count_weekends(start_time, end_time)

            # Trading days (weekdays only)
            trading_days = duration_days - (weekend_info["full_weekends"] * 2)

            return {
                "symbol": symbol,
                "files": len(files),
                "total_ticks": len(combined_df),
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
                    "avg_spread_points": (
                        round(combined_df["spread_points"].mean(), 2)
                        if "spread_points" in combined_df
                        else None
                    ),
                    "avg_spread_pct": (
                        round(combined_df["spread_pct"].mean(), 4)
                        if "spread_pct" in combined_df
                        else None
                    ),
                    "tick_frequency_per_second": (
                        round(len(combined_df) / duration.total_seconds(), 2)
                        if duration.total_seconds() > 0
                        else 0
                    ),
                },
                "file_size_mb": round(total_size / 1024 / 1024, 2),
                "sessions": (
                    combined_df["session"].value_counts().to_dict()
                    if "session" in combined_df
                    else {}
                ),
            }

        except Exception as e:
            logger.error(f"Error loading symbol info for {symbol}: {e}")
            return {"error": f"Error loading {symbol}: {str(e)}"}

    def get_data_summary(self) -> Dict[str, Dict]:
        """Create overview of all available data"""
        symbols = self.loader.list_available_symbols()
        summary = {}

        logger.info(f"Creating summary for {len(symbols)} symbols...")

        for symbol in symbols:
            summary[symbol] = self.get_symbol_info(symbol)

        return summary

    def get_available_date_range(self, symbol: str) -> Tuple[datetime, datetime]:
        """
        Get available date range for a symbol (fast metadata-only check)
        """
        files = self.loader._get_symbol_files(symbol)
        if not files:
            raise ValueError(f"No data found for symbol {symbol}")

        # Quick check: only read metadata
        first_file = pq.read_table(files[0], columns=["timestamp"])
        last_file = pq.read_table(files[-1], columns=["timestamp"])

        start = pd.to_datetime(first_file["timestamp"][0].as_py())
        end = pd.to_datetime(last_file["timestamp"][-1].as_py())

        return start, end

    def _count_weekends(
        self, start_date: datetime, end_date: datetime
    ) -> Dict[str, any]:
        """Count weekends between two dates"""
        # Number of full weeks
        full_weeks = (end_date - start_date).days // 7

        # Remaining days
        remaining_days = (end_date - start_date).days % 7

        # Check if start/end falls on weekend
        start_weekday = start_date.weekday()
        end_weekday = end_date.weekday()

        # Count Saturdays and Sundays separately
        saturdays = full_weeks
        sundays = full_weeks

        # Check remaining days
        current_date = start_date
        for _ in range(remaining_days + 1):
            if current_date.weekday() == 5:  # Saturday
                saturdays += 1
            elif current_date.weekday() == 6:  # Sunday
                sundays += 1
            current_date += timedelta(days=1)

        # Weekend days (Sat+Sun together)
        weekend_days = saturdays + sundays

        # Number of complete weekends (Sat+Sun pairs)
        full_weekends = min(saturdays, sundays)

        return {
            "full_weekends": full_weekends,
            "saturdays": saturdays,
            "sundays": sundays,
            "total_weekend_days": weekend_days,
            "start_is_weekend": start_weekday >= 5,
            "end_is_weekend": end_weekday >= 5,
        }
