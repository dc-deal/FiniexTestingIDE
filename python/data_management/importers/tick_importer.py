"""
FiniexTestingIDE Tick Data Importer
===================================

Converts MQL5 JSON exports to optimized Parquet files with UTC conversion.
Workflow: Load JSON → Validate → Optimize → UTC Conversion → Save Parquet

Author: FiniexTestingIDE Team
Version: 1.4 (Market Type Support)
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Dict, List, Optional, Set

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from python.configuration.app_config_manager import AppConfigManager
from python.data_management.importers.bar_importer import BarImporter
from python.data_management.index.tick_index_manager import TickIndexManager

# Import duplicate detection
from python.data_management.index.data_loader_exceptions import (
    ArtificialDuplicateException,
    DuplicateReport
)

from python.framework.logging.bootstrap_logger import get_global_logger
from python.framework.utils.market_session_utils import get_session_from_utc_hour
vLog = get_global_logger()


class TickDataImporter:
    """
    Converts MQL5 JSON exports to Parquet format with UTC conversion.

    Main features:
    - JSON → Parquet conversion (10:1 compression)
    - Datatype optimization for performance
    - UTC timezone conversion with manual offset
    - Session recalculation based on UTC
    - Quality checks and cleanup
    - Batch processing with error handling
    - Duplicate prevention with override option
    - Hierarchical directory structure
    - Market type detection (v1.4+)

    Args:
        source_dir (str): Directory with JSON files
        target_dir (str): Target directory for Parquet output
        override (bool): If True, overwrite existing files
        time_offset (int): Manual UTC offset in hours (e.g., -3 for GMT+3)
    """

    VERSION = "1.5"

    def __init__(self, source_dir: str, target_dir: str,
                 override: bool = False, time_offset: int = 0,
                 offset_broker: Optional[str] = None):
        """
        Initialize importer with source and target paths.

        Args:
            source_dir: MQL5 JSON export directory
            target_dir: Parquet target directory
            override: Overwrite existing files
            time_offset: UTC offset in hours
            offset_broker: Apply offset only to files with this broker_type
        """
        self.source_dir = Path(source_dir)
        self.target_dir = Path(target_dir)
        self.target_dir.mkdir(parents=True, exist_ok=True)

        self.override = override
        self.time_offset = time_offset
        self.offset_broker = offset_broker

        # Batch processing statistics
        self.processed_files = 0
        self.total_ticks = 0
        self.errors = []
        self._app_config_loader = AppConfigManager()

        # Track processed broker_types for bar rendering
        self._processed_broker_types: Set[str] = set()

    def _normalize_broker_type(self, broker_type: str) -> str:
        """
        Normalize broker_type for filesystem use.

        Args:
            broker_type: Raw broker_type string

        Returns:
            Filesystem-safe normalized string
        """
        normalized = broker_type.lower().strip()
        # Replace anything not alphanumeric or underscore
        normalized = re.sub(r'[^a-z0-9_]', '_', normalized)
        return normalized

    def process_all_exports(self):
        """
        Finds all TickCollector exports and converts them sequentially.
        Errors do not stop processing of remaining files.
        """
        json_files = list(self.source_dir.glob("*_ticks.json"))

        if not json_files:
            vLog.warning(
                f"No JSON files found in {self.source_dir}. Just rebuilding index.")
            self.rebuild_parquet_index()
            return

        vLog.info("\n" + "=" * 80)
        vLog.info(f"FiniexTestingIDE Tick Data Importer V{self.VERSION}")
        vLog.info("=" * 80)
        vLog.info(f"Found: {len(json_files)} JSON files")
        vLog.info(
            f"Override Mode: {'ENABLED' if self.override else 'DISABLED'}")
        if self.time_offset != 0:
            vLog.info(f"Time Offset: {self.time_offset:+d} hours")
            vLog.warning("⚠️ CRITICAL: After offset ALL TIMES ARE UTC!")
            vLog.warning("⚠️ Sessions will be RECALCULATED based on UTC time!")
        else:
            vLog.info(f"Time Offset: NONE (timestamps remain as-is)")
        vLog.info("=" * 80 + "\n")

        # Sequential processing with error recovery
        for json_file in json_files:
            vLog.info(f"\n📄 Processing: {json_file.name}")
            try:
                self.convert_json_to_parquet(json_file)
                self.processed_files += 1
            except ArtificialDuplicateException as e:
                # Special handling for duplicate detection
                error_msg = f"DUPLICATE DETECTED in {json_file.name}"
                vLog.error(error_msg)
                vLog.error(str(e))
                self.errors.append(error_msg)
                vLog.info("→ Skipping import (duplicate already exists)")
            except Exception as e:
                error_msg = f"ERROR in {json_file.name}: {str(e)}"
                vLog.error(error_msg)
                self.errors.append(error_msg)

        self.rebuild_parquet_index()

        # === AUTO-TRIGGER BAR RENDERING ===
        # After all ticks imported, render bars automatically
        if self.processed_files > 0:
            self._trigger_bar_rendering()

        self._print_summary()

    def rebuild_parquet_index(self):
        """Rebuild index after successful imports"""
        vLog.info("\n🔄 Rebuilding Parquet index...")
        try:

            index_manager = TickIndexManager(self.target_dir)
            index_manager.build_index(force_rebuild=True)

            symbols = index_manager.list_symbols()
            vLog.info(f"✅ Index rebuilt: {len(symbols)} symbols indexed")

        except Exception as e:
            vLog.error(f"❌ Failed to rebuild index: {e}")
            vLog.error("   Index may be outdated - run manual rebuild!")

    def convert_json_to_parquet(self, json_file: Path):
        """
        Converts single JSON file to optimized Parquet with UTC conversion.

        Pipeline:
        1. Load JSON and validate structure
        2. Create DataFrame and optimize datatypes
        3. Apply time offset (if set)
        4. Recalculate sessions (if offset applied)
        5. Quality checks
        6. Check for existing duplicates (with override support)
        7. Save as Parquet with metadata
        """

        # ===========================================
        # 1. LOAD AND VALIDATE JSON
        # ===========================================

        with open(json_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        if "ticks" not in data or "metadata" not in data:
            raise ValueError(
                "Invalid JSON structure - missing 'ticks' or 'metadata'")

        ticks = data["ticks"]
        metadata = data["metadata"]

        if not ticks:
            vLog.warning(f"No ticks in {json_file.name}")
            return

        # ===========================================
        # 2. DISPLAY BROKER METADATA (FIXED)
        # ===========================================

        vLog.info(f"📊 Broker Metadata:")

        # Get data version to show in "not available" messages
        data_version = metadata.get('data_format_version', 'unknown')

        # Detected Offset (available since v1.0.5)
        detected_offset = metadata.get('broker_utc_offset_hours', None)
        if detected_offset is not None:
            sign = '+' if detected_offset >= 0 else ''
            vLog.info(f"   Detected Offset: GMT{sign}{detected_offset}")
        else:
            vLog.info(
                f"   Detected Offset: Not available (pre v1.0.5 data, version: {data_version})")

        # Local Device Time (planned for v1.0.5+, but not yet implemented in MQL5)
        local_device = metadata.get('local_device_time', None)
        if local_device:
            vLog.info(f"   Local Device:    {local_device}")
        else:
            vLog.info(f"   Local Device:    Not available (pre v1.0.5 data)")

        # Broker Time (planned for v1.0.5+, but not yet implemented in MQL5)
        broker_time = metadata.get('broker_server_time', None)
        if broker_time:
            vLog.info(f"   Broker Time:     {broker_time}")
        else:
            vLog.info(f"   Broker Time:     Not available (pre v1.0.5 data)")

        # ===========================================
        # 3. CREATE AND OPTIMIZE DATAFRAME
        # ===========================================

        df = pd.DataFrame(ticks)
        df = self._optimize_datatypes(df)

        # Parse timestamps as timezone-naive
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"])

        # ===========================================
        # 4. APPLY TIME OFFSET
        # ===========================================
        broker_type = metadata.get("broker_type")

        if broker_type is None:
            raise ValueError(
                f"Missing 'broker_type' in metadata. "
                f"File format must be v1.1.0+ with explicit broker_type field."
            )
        broker_type_normalized = self._normalize_broker_type(broker_type)

        should_apply_offset = (
            self.time_offset != 0 and
            self.offset_broker is not None and
            broker_type_normalized == self.offset_broker
        )

        if should_apply_offset:
            df = self._apply_time_offset(df)
            df = self._recalculate_sessions(df)
            vLog.info(
                f"   ✅ Time offset {self.time_offset:+d}h applied (broker_type={broker_type_normalized})")
            vLog.info(f"   ✅ Sessions recalculated based on UTC time")
        elif self.time_offset != 0:
            vLog.info(
                f"   ℹ️  No offset applied (broker_type={broker_type_normalized} != {self.offset_broker})")

        # ===========================================
        # 6. QUALITY CHECKS
        # ===========================================

        self._processed_broker_types.add(broker_type_normalized)
        df = self._quality_checks(df)
        df = df.sort_values("timestamp").reset_index(drop=True)

        # ===========================================
        # 7. PREPARE PARQUET OUTPUT
        # CHANGED: New path construction!
        # ===========================================

        symbol = metadata.get("symbol", "UNKNOWN")
        start_time = pd.to_datetime(metadata.get(
            "start_time", datetime.now(timezone.utc)))

        # Extract data_format_version
        data_format_version = metadata.get("data_format_version", "1.0.0")

        # market_type with fallback logic
        market_type = self._determine_market_type(
            metadata, data_format_version)

        # NEW STRUCTURE: broker_type / ticks / symbol
        target_path = self.target_dir / broker_type_normalized / "ticks" / symbol
        target_path.mkdir(parents=True, exist_ok=True)

        parquet_name = f"{symbol}_{start_time.strftime('%Y%m%d_%H%M%S')}.parquet"
        parquet_path = target_path / parquet_name

        # Metadata for Parquet header
        parquet_metadata = {
            "source_file": json_file.name,
            "symbol": symbol,
            "broker": metadata.get("broker", "unknown"),
            "data_format_version": data_format_version,
            "broker_type": broker_type_normalized,
            "market_type": market_type,
            "processed_at": datetime.now(timezone.utc).isoformat(),
            "tick_count": str(len(df)),
            "importer_version": self.VERSION,
            "user_time_offset_hours": str(self.time_offset if broker_type_normalized == self.offset_broker else 0),
            "utc_conversion_applied": "true" if (self.time_offset != 0 and broker_type_normalized == self.offset_broker) else "false",
        }

        # ===========================================
        # 8. CHECK FOR EXISTING DUPLICATES
        # ===========================================

        vLog.debug(f"Checking for existing duplicates...")
        duplicate_report = self._check_for_existing_duplicate(
            json_file.name,
            broker_type_normalized,
            symbol,
            df,
            parquet_path
        )

        if duplicate_report:
            if self.override:
                vLog.warning(f"⚠️  Override enabled - deleting existing file")
                for dup_file in duplicate_report.duplicate_files:
                    dup_file.unlink()
                    vLog.info(f"   🗑️  Deleted: {dup_file.name}")
            else:
                raise ArtificialDuplicateException(duplicate_report)

        # ===========================================
        # 9. WRITE PARQUET
        # ===========================================

        try:
            table = pa.Table.from_pandas(df)
            table = table.replace_schema_metadata(parquet_metadata)
            pq.write_table(table, parquet_path, compression="snappy")

            json_size = json_file.stat().st_size
            parquet_size = parquet_path.stat().st_size
            compression_ratio = json_size / parquet_size if parquet_size > 0 else 0

            if self._app_config_loader.get_move_processed_files:
                finished_dir = Path("./data/finished/")
                finished_dir.mkdir(exist_ok=True)
                finished_file = finished_dir / json_file.name
                json_file.rename(finished_file)
                vLog.info(f"→ Moved {json_file.name} to finished/")

            self.total_ticks += len(df)

            time_suffix = " (UTC)" if should_apply_offset else ""
            vLog.info(
                f"✅ {broker_type_normalized}/ticks/{symbol}/{parquet_name}: {len(df):,} Ticks{time_suffix}, "
                f"Compression {compression_ratio:.1f}:1 "
                f"({json_size/1024/1024:.1f}MB → {parquet_size/1024/1024:.1f}MB)"
            )

            vLog.debug(
                f"   market_type={market_type}, version={data_format_version}")

        except Exception as e:
            vLog.error(f"ERROR writing {parquet_path}")
            vLog.error(f"Original Error: {str(e)}")
            vLog.error(f"Error Type: {type(e)}")
            raise

    def _determine_market_type(self, metadata: Dict, data_format_version: str) -> str:
        """
        Determines market type based on metadata and version.

        Logic:
        - Version < 1.1.0: Implicitly forex_cfd (all old MT5 data)
        - Version >= 1.1.0: Explicitly from metadata['market_type']

        Args:
            metadata: JSON metadata from source file
            data_format_version: Data format version

        Returns:
            market_type string: 'forex_cfd', 'crypto_spot', etc.
        """
        # Parse version for comparison
        try:
            version_parts = [int(x) for x in data_format_version.split('.')]
            version_tuple = tuple(
                version_parts + [0] * (3 - len(version_parts)))
        except (ValueError, AttributeError):
            version_tuple = (1, 0, 0)

        # Fallback for old versions
        if version_tuple < (1, 1, 0):
            return 'forex_cfd'

        # From 1.1.0+: Explicitly from metadata
        return metadata.get('market_type', 'unknown')

    def _apply_time_offset(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Applies manual time offset to timestamps.

        Args:
            df: DataFrame with 'timestamp' column

        Returns:
            DataFrame with adjusted timestamps
        """
        if self.time_offset == 0:
            return df

        if "timestamp" not in df.columns:
            return df

        # Store original for logging
        original_first = df["timestamp"].iloc[0]
        original_last = df["timestamp"].iloc[-1]

        # Apply offset
        offset_timedelta = pd.Timedelta(hours=self.time_offset)
        df["timestamp"] = df["timestamp"] - offset_timedelta

        utc_first = df["timestamp"].iloc[0]
        utc_last = df["timestamp"].iloc[-1]

        vLog.info(f"   🕐 Time Offset Applied: {self.time_offset:+d} hours")
        vLog.info(f"      Original: {original_first} → {original_last}")
        vLog.info(f"      UTC:      {utc_first} → {utc_last}")

        return df

    def _recalculate_sessions(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Recalculates trading sessions based on UTC time.
        """

        if 'session' in df.columns:
            df['session'] = df['session'].apply(
                lambda x: x.value if hasattr(x, 'value') else str(x)
            )
        return df

    def _check_for_existing_duplicate(
        self,
        source_json_name: str,
        broker_type: str,
        symbol: str,
        new_df: pd.DataFrame,
        target_path: Path
    ) -> Optional[DuplicateReport]:
        """
        Check if Parquet file already exists with same source.

        Args:
            source_json_name: Name of source JSON file
            broker_type: Broker type identifier
            symbol: Trading symbol
            new_df: DataFrame being imported
            target_path: Target parquet path

        Returns:
            DuplicateReport if duplicate found, None otherwise
        """

        # OPTION 2: Cross-Collector Search (wie in Doku)
        search_pattern = f"*/ticks/{symbol}/{symbol}_*.parquet"
        existing_files = list(self.target_dir.glob(search_pattern))

        if not existing_files:
            return None

        for existing_file in existing_files:
            try:
                parquet_file = pq.ParquetFile(existing_file)
                metadata_raw = parquet_file.metadata.metadata

                existing_metadata = {
                    key.decode('utf-8') if isinstance(key, bytes) else key:
                    value.decode('utf-8') if isinstance(value,
                                                        bytes) else value
                    for key, value in metadata_raw.items()
                }

                existing_source = existing_metadata.get('source_file', '')
                # legacy compability : data_collector
                existing_broker = existing_metadata.get(
                    'broker_type') or existing_metadata.get('data_collector', 'unknown')

                if existing_source == source_json_name:
                    relative_path = existing_file.relative_to(self.target_dir)
                    collector_path = relative_path.parts[0] if len(
                        relative_path.parts) > 0 else "unknown"

                    vLog.warning(
                        f"⚠️  Found existing Parquet: {collector_path}/{symbol}/{existing_file.name}"
                    )
                    vLog.warning(
                        f"    Existing: broker_type='{existing_broker}' | "
                        f"Importing: broker_type='{broker_type}'"
                    )

                    existing_df = pd.read_parquet(existing_file)

                    return DuplicateReport(
                        source_file=source_json_name,
                        duplicate_files=[existing_file],
                        tick_counts=[len(existing_df)],
                        time_ranges=[
                            (existing_df['timestamp'].min(),
                             existing_df['timestamp'].max())
                        ],
                        file_sizes_mb=[
                            existing_file.stat().st_size / (1024 * 1024)],
                        metadata=[existing_metadata]
                    )

            except Exception as e:
                vLog.warning(
                    f"Could not read metadata from {existing_file.name}: {e}")

        return None

    def _optimize_datatypes(self, df: pd.DataFrame) -> pd.DataFrame:
        """Optimizes DataFrame datatypes for performance."""

        float_cols = ["bid", "ask", "last", "spread_pct", "real_volume"]
        for col in float_cols:
            if col in df.columns:
                df[col] = df[col].astype("float32")

        int_cols = ["tick_volume", "chart_tick_volume", "spread_points"]
        for col in int_cols:
            if col in df.columns:
                df[col] = df[col].astype("int32")

        return df

    def _quality_checks(self, df: pd.DataFrame) -> pd.DataFrame:
        """Performs quality checks on tick data."""

        # Check 1: Invalid Prices
        invalid_prices = df[(df["bid"] <= 0) | (df["ask"] <= 0)]
        if len(invalid_prices) > 0:
            vLog.warning(
                f"⚠️  {len(invalid_prices)} ticks with invalid prices")

        # Check 2: Extreme Spreads
        if "spread_pct" in df.columns:
            extreme_spreads = df[df["spread_pct"] > 5.0]
            if len(extreme_spreads) > 0:
                vLog.warning(
                    f"⚠️  {len(extreme_spreads)} ticks with extreme spreads")

        # Check 3: Price Jumps
        df["bid_pct_change"] = df["bid"].pct_change().abs() * 100
        large_jumps = df[df["bid_pct_change"] > 10.0]
        if len(large_jumps) > 0:
            vLog.warning(
                f"⚠️  {len(large_jumps)} ticks with large price jumps")

        df = df.drop(columns=["bid_pct_change"], errors="ignore")
        return df

    def _print_summary(self):
        """Prints summary of batch processing."""

        vLog.info("\n" + "=" * 80)
        vLog.info("PROCESSING SUMMARY")
        vLog.info("=" * 80)
        vLog.info(f"✅ Processed files: {self.processed_files}")
        vLog.info(f"✅ Total ticks: {self.total_ticks:,}")
        if self.time_offset != 0:
            vLog.info(
                f"✅ Time Offset: {self.time_offset:+d} hours (ALL TIMES ARE UTC!)")
        vLog.info(f"❌ Errors: {len(self.errors)}")

        if self.errors:
            vLog.error("\nERROR LIST:")
            for error in self.errors:
                vLog.error(f"  - {error}")

        vLog.info("=" * 80 + "\n")

    def _trigger_bar_rendering(self):
        """
        Trigger automatic bar rendering after tick import.
        Renders bars for all broker_types that were processed.
        """
        vLog.info("\n" + "=" * 80)
        vLog.info("🔄 AUTO-TRIGGERING BAR RENDERING")
        vLog.info("=" * 80)

        try:
            bar_importer = BarImporter(str(self.target_dir))

            for broker_type in self._processed_broker_types:
                vLog.info(f"\n📊 Rendering bars for broker_type: {broker_type}")
                bar_importer.render_bars_for_all_symbols(
                    broker_type=broker_type,
                    clean_mode=True
                )

            vLog.info("✅ Bar rendering completed!")

        except Exception as e:
            vLog.error(f"❌ Bar rendering failed: {e}")
            vLog.error("   You can manually trigger it later with:")
            vLog.error("   python -m bar_importer")
