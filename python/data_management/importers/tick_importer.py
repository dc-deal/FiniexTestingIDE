"""
FiniexTestingIDE Tick Data Importer
===================================

Converts MQL5 JSON exports to optimized Parquet files with UTC conversion.
Workflow: Load JSON → Validate → Optimize → UTC Conversion → Save Parquet

Author: FiniexTestingIDE Team
Version: 1.6 (Import Config Isolation + Source Metadata)
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

from python.configuration.import_config_manager import ImportConfigManager
from python.configuration.market_config_manager import MarketConfigManager
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

    VERSION = "1.6"

    def __init__(self, source_dir: str, target_dir: str,
                 override: bool = False,
                 offset_registry: Optional[Dict[str, int]] = None,
                 move_processed_files: bool = True,
                 finished_dir: Optional[str] = None,
                 auto_render_bars: bool = True):
        """
        Initialize importer with source and target paths.

        Args:
            source_dir: MQL5 JSON export directory
            target_dir: Parquet target directory (import_output)
            override: Overwrite existing files
            offset_registry: Per-broker offset mapping {broker_type: offset_hours}
            move_processed_files: Move JSON to finished_dir after import
            finished_dir: Directory for processed JSON files
            auto_render_bars: Automatically render bars after tick import
        """
        self.source_dir = Path(source_dir)
        self.target_dir = Path(target_dir)
        self.target_dir.mkdir(parents=True, exist_ok=True)

        self.override = override
        self._offset_registry: Dict[str, int] = offset_registry or {}
        self._move_processed_files = move_processed_files
        self._finished_dir = Path(finished_dir) if finished_dir else None
        self._auto_render_bars = auto_render_bars

        # Batch processing statistics
        self.processed_files = 0
        self.total_ticks = 0
        self.errors = []

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
        if self._offset_registry:
            for bt, offset in self._offset_registry.items():
                if offset != 0:
                    vLog.info(f"Offset: {bt} → {offset:+d}h (UTC conversion)")
                else:
                    vLog.info(f"Offset: {bt} → 0h (no conversion)")
        else:
            vLog.info(f"Offset Registry: EMPTY (no offsets configured)")
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
        if self.processed_files > 0 and self._auto_render_bars:
            self._trigger_bar_rendering()

        self._print_summary()

    def rebuild_parquet_index(self):
        """Rebuild index after successful imports"""
        vLog.info("\n🔄 Rebuilding Parquet index...")
        try:

            index_manager = TickIndexManager(data_dir=str(self.target_dir))
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
        # 4. APPLY TIME OFFSET (from registry)
        # ===========================================
        broker_type_normalized = self._validate_broker_type(
            metadata)

        # Resolve offset for this file's broker_type from registry
        file_offset = self._offset_registry.get(broker_type_normalized, 0)
        should_apply_offset = file_offset != 0

        if should_apply_offset:
            df = self._apply_time_offset(df, file_offset)
            df = self._recalculate_sessions(df)
            vLog.info(
                f"   ✅ Time offset {file_offset:+d}h applied (broker_type={broker_type_normalized})")
            vLog.info(f"   ✅ Sessions recalculated based on UTC time")
        else:
            vLog.info(
                f"   ℹ️  No offset for broker_type={broker_type_normalized} (0h in registry)")

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

        # Extract data_format_version (for metadata only)
        data_format_version = metadata.get("data_format_version", "1.0.0")

        # Get market_type from MarketConfigManager (Single Source of Truth)
        market_config = MarketConfigManager()
        market_type = market_config.get_market_type(
            broker_type_normalized).value

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
            "user_time_offset_hours": str(file_offset),
            "utc_conversion_applied": "true" if should_apply_offset else "false",
        }

        # Preserve original MQL5 metadata for traceability (source_meta_ prefix)
        _NESTED_META_KEYS = {"symbol_info", "collection_settings", "error_tracking"}
        for meta_key, meta_value in metadata.items():
            if meta_key in _NESTED_META_KEYS:
                parquet_metadata[f"source_meta_{meta_key}"] = json.dumps(meta_value)
            elif meta_key not in ("symbol", "broker", "ticks"):
                parquet_metadata[f"source_meta_{meta_key}"] = str(meta_value)

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

            if self._move_processed_files and self._finished_dir:
                self._finished_dir.mkdir(exist_ok=True)
                finished_file = self._finished_dir / json_file.name
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

    def _apply_time_offset(self, df: pd.DataFrame, offset_hours: int) -> pd.DataFrame:
        """
        Applies time offset to timestamps for UTC conversion.

        Args:
            df: DataFrame with 'timestamp' column
            offset_hours: Hours to add (e.g. -3 for GMT+3 → UTC subtracts 3h)

        Returns:
            DataFrame with adjusted timestamps
        """
        if offset_hours == 0:
            return df

        if "timestamp" not in df.columns:
            return df

        # Store original for logging
        original_first = df["timestamp"].iloc[0]
        original_last = df["timestamp"].iloc[-1]

        # Apply offset (e.g. offset=-3 → subtract 3h from timestamp)
        offset_timedelta = pd.Timedelta(hours=offset_hours)
        df["timestamp"] = df["timestamp"] + offset_timedelta

        utc_first = df["timestamp"].iloc[0]
        utc_last = df["timestamp"].iloc[-1]

        vLog.info(f"   🕐 Time Offset Applied: {offset_hours:+d} hours")
        vLog.info(f"      Original: {original_first} → {original_last}")
        vLog.info(f"      UTC:      {utc_first} → {utc_last}")

        return df

    def _recalculate_sessions(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Recalculates trading sessions based on UTC time.

        Args:
            df: DataFrame with 'timestamp' column (must be UTC after offset)

        Returns:
            DataFrame with corrected session labels
        """
        if 'session' in df.columns and 'timestamp' in df.columns:
            df['session'] = df['timestamp'].dt.hour.map(
                lambda h: get_session_from_utc_hour(h).value
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
        active_offsets = {bt: off for bt, off in self._offset_registry.items() if off != 0}
        if active_offsets:
            offsets_str = ", ".join(f"{bt}: {off:+d}h" for bt, off in active_offsets.items())
            vLog.info(f"✅ Offsets applied: {offsets_str} (UTC converted)")
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
            bar_importer = BarImporter(data_dir=str(self.target_dir))

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

    def _validate_broker_type(self, metadata: dict) -> str:
        """
        Validate broker_type exists and is mapped in market_config.json.

        Args:
            metadata: JSON metadata from source file

        Returns:
            Normalized broker_type string

        Raises:
            ValueError: If broker_type missing or not mapped
        """
        market_config = MarketConfigManager()
        available_brokers = market_config.get_all_broker_types()

        # Build available brokers string for error messages
        broker_list_str = "\n".join(
            f"     • {bt} → {market_config.get_market_type(bt).value}"
            for bt in available_brokers
        )

        # Check 1: broker_type must exist in metadata
        broker_type = metadata.get("broker_type", None)

        # Check for legacy data_collector field
        if (broker_type is None):
            broker_type = metadata.get("data_collector", None)

        # if broker_type could not found at all:
        if broker_type is None:
            raise ValueError(
                f"Missing 'broker_type' (or LEGACY identifier data_collector) in JSON metadata.\n\n"
                f"   This appears to be a LEGACY file.\n\n"
                f"   To enable import, add the following to the JSON metadata section:\n"
                f"     \"broker_type\": ... "
                f"   Available broker_types in market_config.json:\n"
                f"{broker_list_str}"
            )

        # Normalize broker_type
        broker_type_normalized = self._normalize_broker_type(broker_type)

        # Check 2: broker_type must be mapped in market_config.json
        if broker_type_normalized not in available_brokers:
            raise ValueError(
                f"Unknown broker_type '{broker_type_normalized}'.\n\n"
                f"   Not found in configs/market_config.json.\n\n"
                f"   Available broker_types:\n"
                f"{broker_list_str}\n\n"
                f"   To add a new broker_type, update configs/market_config.json:\n"
                f"     {{\n"
                f"       \"broker_type\": \"{broker_type_normalized}\",\n"
                f"       \"market_type\": \"forex\",  // or \"crypto\"\n"
                f"       \"broker_config_path\": \"./configs/brokers/{broker_type_normalized}/config.json\"\n"
                f"     }}"
            )

        return broker_type_normalized
