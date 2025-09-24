"""
FiniexTestingIDE - Tick Data Preparator
Bridges between your existing DataLoader and the new Blackbox system
"""

import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any, Optional, Tuple, Iterator
import logging

# Import your existing data loader
from python.blackbox import TickData
from python.data_loader import TickDataLoader

logger = logging.getLogger(__name__)


class TickDataPreparator:
    """
    Prepares tick data from your existing DataLoader for the Blackbox system

    Main job: Convert DataFrame rows to TickData objects efficiently
    """

    def __init__(self, data_loader: TickDataLoader):
        self.data_loader = data_loader
        self.available_symbols = None

    def get_available_symbols(self) -> List[str]:
        """Get list of available symbols from your data loader"""
        if self.available_symbols is None:
            self.available_symbols = self.data_loader.list_available_symbols()
        return self.available_symbols

    def get_symbol_info(self, symbol: str) -> Dict[str, Any]:
        """Get info about a symbol (delegates to your existing loader)"""
        return self.data_loader.get_symbol_info(symbol)

    def prepare_tick_sequence(
        self,
        symbol: str,
        data_mode: str = "realistic",
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        max_ticks: Optional[int] = None,
    ) -> Iterator[TickData]:
        """
        Prepare a sequence of ticks for blackbox processing

        Args:
            symbol: Currency pair (e.g., 'EURUSD')
            data_mode: 'clean', 'realistic', or 'raw' (your quality modes)
            start_date: ISO date string or None
            end_date: ISO date string or None
            max_ticks: Limit number of ticks (for testing)

        Yields:
            TickData objects one by one (memory efficient)
        """

        logger.info(f"Preparing tick sequence for {symbol} (mode: {data_mode})")

        # Load data using your existing loader
        df = self.data_loader.load_symbol_data(
            symbol=symbol, start_date=start_date, end_date=end_date, use_cache=True
        )

        if df.empty:
            logger.warning(f"No data found for {symbol}")
            return

        logger.info(f"Loaded {len(df):,} ticks for {symbol}")

        # Apply data mode filtering if needed
        # (Your DataLoader might already handle this, but we can add extra filtering)
        if data_mode == "clean":
            df = self._apply_clean_filtering(df)
        elif data_mode == "realistic":
            df = self._apply_realistic_filtering(df)
        # 'raw' mode = no filtering

        # Limit ticks if requested
        if max_ticks and len(df) > max_ticks:
            df = df.head(max_ticks)
            logger.info(f"Limited to {max_ticks} ticks for testing")

        # Convert DataFrame rows to TickData objects
        tick_count = 0
        for _, row in df.iterrows():

            # Convert timestamp to ISO string if it's not already
            if isinstance(row["timestamp"], pd.Timestamp):
                timestamp_str = row["timestamp"].isoformat()
            else:
                timestamp_str = str(row["timestamp"])

            # Create TickData object
            tick = TickData(
                timestamp=timestamp_str,
                symbol=symbol,
                bid=float(row["bid"]),
                ask=float(row["ask"]),
                volume=float(row.get("volume", row.get("tick_volume", 0))),
            )

            yield tick
            tick_count += 1

            # Progress logging for large datasets
            if tick_count % 10000 == 0:
                logger.debug(f"Prepared {tick_count:,} ticks...")

        logger.info(f"✓ Prepared {tick_count:,} ticks for {symbol}")

    def prepare_warmup_ticks(
        self, symbol: str, warmup_bars_needed: int, data_mode: str = "realistic"
    ) -> List[TickData]:
        """
        Prepare warmup ticks for blackbox initialization

        Args:
            symbol: Currency pair
            warmup_bars_needed: Number of historical bars needed
            data_mode: Quality mode

        Returns:
            List of TickData for warmup
        """

        logger.info(f"Preparing {warmup_bars_needed} warmup ticks for {symbol}")

        # Load recent data
        df = self.data_loader.load_symbol_data(symbol=symbol, use_cache=True)

        if df.empty:
            logger.error(f"No data available for {symbol}")
            return []

        # Take the first N ticks for warmup (chronologically first)
        warmup_df = df.head(warmup_bars_needed + 100)  # Extra buffer for safety

        # Apply filtering
        if data_mode == "clean":
            warmup_df = self._apply_clean_filtering(warmup_df)
        elif data_mode == "realistic":
            warmup_df = self._apply_realistic_filtering(warmup_df)

        # Convert to TickData objects
        warmup_ticks = []
        for _, row in warmup_df.iterrows():

            timestamp_str = (
                row["timestamp"].isoformat()
                if isinstance(row["timestamp"], pd.Timestamp)
                else str(row["timestamp"])
            )

            tick = TickData(
                timestamp=timestamp_str,
                symbol=symbol,
                bid=float(row["bid"]),
                ask=float(row["ask"]),
                volume=float(row.get("volume", row.get("tick_volume", 0))),
            )
            warmup_ticks.append(tick)

        # Take exactly what we need
        final_warmup = warmup_ticks[:warmup_bars_needed]
        logger.info(f"✓ Prepared {len(final_warmup)} warmup ticks")

        return final_warmup

    def prepare_test_and_warmup_split(
        self,
        symbol: str,
        warmup_bars_needed: int,
        test_ticks_count: Optional[int] = None,
        data_mode: str = "realistic",
    ) -> Tuple[List[TickData], Iterator[TickData]]:
        """
        Prepare both warmup and test data in one go

        Returns:
            Tuple of (warmup_ticks, test_tick_iterator)
        """

        logger.info(f"Preparing warmup + test split for {symbol}")

        # Load all data
        df = self.data_loader.load_symbol_data(symbol=symbol, use_cache=True)

        if df.empty or len(df) < warmup_bars_needed:
            logger.error(
                f"Insufficient data for {symbol}: need {warmup_bars_needed}, have {len(df)}"
            )
            return [], iter([])

        # Apply filtering
        if data_mode == "clean":
            df = self._apply_clean_filtering(df)
        elif data_mode == "realistic":
            df = self._apply_realistic_filtering(df)

        # Split into warmup and test portions
        warmup_df = df.head(warmup_bars_needed)
        test_start_idx = warmup_bars_needed

        if test_ticks_count:
            test_df = df.iloc[test_start_idx : test_start_idx + test_ticks_count]
        else:
            test_df = df.iloc[test_start_idx:]

        logger.info(f"Split: {len(warmup_df)} warmup + {len(test_df)} test ticks")

        # Convert warmup to list
        warmup_ticks = [
            TickData(
                timestamp=(
                    row["timestamp"].isoformat()
                    if isinstance(row["timestamp"], pd.Timestamp)
                    else str(row["timestamp"])
                ),
                symbol=symbol,
                bid=float(row["bid"]),
                ask=float(row["ask"]),
                volume=float(row.get("volume", row.get("tick_volume", 0))),
            )
            for _, row in warmup_df.iterrows()
        ]

        # Convert test data to iterator (memory efficient)
        def test_tick_generator():
            for _, row in test_df.iterrows():
                timestamp_str = (
                    row["timestamp"].isoformat()
                    if isinstance(row["timestamp"], pd.Timestamp)
                    else str(row["timestamp"])
                )
                yield TickData(
                    timestamp=timestamp_str,
                    symbol=symbol,
                    bid=float(row["bid"]),
                    ask=float(row["ask"]),
                    volume=float(row.get("volume", row.get("tick_volume", 0))),
                )

        return warmup_ticks, test_tick_generator()

    def _apply_clean_filtering(self, df: pd.DataFrame) -> pd.DataFrame:
        """Apply clean mode filtering (most restrictive)"""

        logger.debug("Applying clean mode filtering")
        initial_count = len(df)

        # Remove any rows with extreme spreads (if spread data available)
        if "spread_pct" in df.columns:
            df = df[df["spread_pct"] <= 0.5]  # Max 0.5% spread

        # Remove any potential outliers
        if "bid" in df.columns and "ask" in df.columns:
            # Remove rows where bid/ask are obviously wrong
            df = df[df["bid"] > 0]
            df = df[df["ask"] > 0]
            df = df[df["ask"] >= df["bid"]]  # Ask must be >= bid

        # Remove any rows with suspicious timestamps (if duplicated)
        df = df.drop_duplicates(subset=["timestamp"], keep="last")

        removed_count = initial_count - len(df)
        if removed_count > 0:
            logger.debug(
                f"Clean filtering removed {removed_count} ticks ({removed_count/initial_count:.1%})"
            )

        return df

    def _apply_realistic_filtering(self, df: pd.DataFrame) -> pd.DataFrame:
        """Apply realistic mode filtering (moderate)"""

        logger.debug("Applying realistic mode filtering")
        initial_count = len(df)

        # Only remove obviously broken data
        if "bid" in df.columns and "ask" in df.columns:
            df = df[df["bid"] > 0]
            df = df[df["ask"] > 0]
            df = df[df["ask"] >= df["bid"]]

        # Keep most market anomalies (they're realistic)
        # Just remove extreme outliers that are likely data errors
        if "spread_pct" in df.columns:
            df = df[df["spread_pct"] <= 5.0]  # Allow up to 5% spread

        removed_count = initial_count - len(df)
        if removed_count > 0:
            logger.debug(
                f"Realistic filtering removed {removed_count} ticks ({removed_count/initial_count:.1%})"
            )

        return df

    def create_test_scenario(
        self,
        symbol: str,
        scenario_name: str,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        max_duration_hours: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Create a named test scenario for repeatable testing

        Args:
            symbol: Currency pair
            scenario_name: Human-readable name
            start_date: Start date or None for beginning
            end_date: End date or None for all data
            max_duration_hours: Limit test duration

        Returns:
            Scenario configuration dictionary
        """

        # Get data info
        symbol_info = self.get_symbol_info(symbol)

        if "error" in symbol_info:
            logger.error(f"Cannot create scenario for {symbol}: {symbol_info['error']}")
            return {}

        # Calculate actual date range
        actual_start = start_date if start_date else symbol_info["date_range"]["start"]
        actual_end = end_date if end_date else symbol_info["date_range"]["end"]

        # Estimate tick count (for performance planning)
        total_ticks = symbol_info["total_ticks"]
        if start_date or end_date:
            # Rough estimation based on date range
            total_days = symbol_info["date_range"]["days"]
            if total_days > 0:
                ticks_per_day = total_ticks / total_days

                if start_date and end_date:
                    scenario_days = (
                        pd.to_datetime(end_date) - pd.to_datetime(start_date)
                    ).days
                    estimated_ticks = int(ticks_per_day * scenario_days)
                else:
                    estimated_ticks = total_ticks
            else:
                estimated_ticks = total_ticks
        else:
            estimated_ticks = total_ticks

        scenario = {
            "name": scenario_name,
            "symbol": symbol,
            "date_range": {"start": actual_start, "end": actual_end},
            "estimated_ticks": estimated_ticks,
            "estimated_duration_hours": max_duration_hours
            or (estimated_ticks / 3600),  # Rough estimate
            "data_quality": {
                "overall_score": symbol_info.get("statistics", {}).get(
                    "overall_quality_score", "unknown"
                ),
                "avg_spread_points": symbol_info.get("statistics", {}).get(
                    "avg_spread_points", "unknown"
                ),
            },
            "recommended_warmup_bars": min(
                200, estimated_ticks // 100
            ),  # 1% of data or 200 max
            "created_at": datetime.now().isoformat(),
        }

        logger.info(
            f"Created scenario '{scenario_name}': {estimated_ticks:,} ticks, {symbol}"
        )
        return scenario


# Convenience functions for easy usage
def quick_prepare_for_testing(
    symbol: str = "EURUSD", max_ticks: int = 1000
) -> Tuple[List[TickData], Iterator[TickData]]:
    """
    Quick function to prepare data for testing (convenience wrapper)
    """

    # Initialize with your data
    loader = TickDataLoader("./data/processed/")
    preparator = TickDataPreparator(loader)

    # Prepare a small test dataset
    warmup_ticks, test_ticks = preparator.prepare_test_and_warmup_split(
        symbol=symbol,
        warmup_bars_needed=50,  # Small warmup for testing
        test_ticks_count=max_ticks,
        data_mode="realistic",
    )

    return warmup_ticks, test_ticks


# Example usage and testing
if __name__ == "__main__":
    import time

    logging.basicConfig(level=logging.INFO)

    # Initialize with your existing data loader
    loader = TickDataLoader("./data/processed/")
    preparator = TickDataPreparator(loader)

    print("=== Available Symbols ===")
    symbols = preparator.get_available_symbols()
    print(f"Found: {symbols}")

    if not symbols:
        print("❌ No data found! Make sure you've run the tick importer first:")
        print("   python python/tick_importer.py")
        exit(1)

    # Use first available symbol for demo
    test_symbol = symbols[0]
    print(f"\n=== Testing with {test_symbol} ===")

    # Get symbol info
    info = preparator.get_symbol_info(test_symbol)
    print(f"Symbol info: {info['total_ticks']:,} ticks available")

    # Create a test scenario
    scenario = preparator.create_test_scenario(
        symbol=test_symbol,
        scenario_name=f"Quick Test - {test_symbol}",
        max_duration_hours=1,
    )
    print(f"Created scenario: {scenario['name']}")
    print(f"Estimated ticks: {scenario['estimated_ticks']:,}")

    # Prepare warmup + test split
    print(f"\n=== Preparing Data Split ===")
    warmup_ticks, test_iterator = preparator.prepare_test_and_warmup_split(
        symbol=test_symbol,
        warmup_bars_needed=100,
        test_ticks_count=500,  # Small test for demo
        data_mode="realistic",
    )

    print(f"✓ Warmup: {len(warmup_ticks)} ticks")
    print(
        f"✓ First warmup tick: {warmup_ticks[0].timestamp} @ {warmup_ticks[0].mid:.5f}"
    )
    print(
        f"✓ Last warmup tick: {warmup_ticks[-1].timestamp} @ {warmup_ticks[-1].mid:.5f}"
    )

    # Test the iterator (process a few test ticks)
    print(f"\n=== Processing Test Ticks ===")
    tick_count = 0
    start_time = time.time()

    for tick in test_iterator:
        tick_count += 1

        if tick_count <= 5:  # Show first 5
            print(
                f"Tick {tick_count}: {tick.timestamp} @ {tick.mid:.5f} (spread: {tick.ask - tick.bid:.5f})"
            )
        elif tick_count == 6:
            print("...")

        if tick_count >= 500:  # Don't process all in demo
            break

    processing_time = time.time() - start_time
    print(f"✓ Processed {tick_count} ticks in {processing_time:.3f}s")
    print(f"✓ Rate: {tick_count/processing_time:.1f} ticks/second")

    # Quick prepare function test
    print(f"\n=== Testing Quick Prepare Function ===")
    quick_warmup, quick_test = quick_prepare_for_testing(test_symbol, max_ticks=100)
    print(f"✓ Quick prepare: {len(quick_warmup)} warmup ticks ready")

    quick_count = sum(1 for _ in quick_test)
    print(f"✓ Quick prepare: {quick_count} test ticks ready")

    print(f"\n🎉 TickDataPreparator is ready for your blackbox system!")
