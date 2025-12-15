"""
FiniexTestingIDE - Data Inspector
Inspect tick and bar data: metadata, schema, and sample rows

Location: python/data_management/index/data_inspector.py

Usage:
    inspector = DataInspector(tick_index, bar_index)
    
    # Inspect ticks
    result = inspector.inspect_ticks('EURUSD')
    inspector.print_inspection(result)
    
    # Inspect bars
    result = inspector.inspect_bars('EURUSD', 'M5')
    inspector.print_inspection(result)
"""

from pathlib import Path
from typing import Dict, Optional, Any
import json

import pandas as pd
import pyarrow.parquet as pq

from python.data_management.index.tick_index_manager import TickIndexManager
from python.data_management.index.bars_index_manager import BarsIndexManager

from python.framework.logging.bootstrap_logger import get_global_logger
vLog = get_global_logger()


class DataInspector:
    """
    Inspect tick and bar data for development and debugging.

    Provides:
    - Parquet metadata inspection (all fields)
    - Schema information
    - Sample rows (first 10)
    - File statistics
    """

    # Skip arrow metadatakeys (example:
    # Arrow scema is a serialized flatbuffer, so you don't want to read this.)
    SKIP_KEYS = {
        "ARROW:schema",
    }

    def __init__(
        self,
        tick_index_manager: TickIndexManager,
        bar_index_manager: Optional[BarsIndexManager] = None
    ):
        """
        Initialize data inspector.

        Args:
            tick_index_manager: Tick index manager instance
            bar_index_manager: Optional bar index manager instance
        """
        self._tick_index = tick_index_manager
        self._bar_index = bar_index_manager

    def inspect_ticks(self, symbol: str) -> Dict[str, Any]:
        """
        Inspect tick data for a symbol.

        Loads FIRST tick file for inspection (not all files).

        Args:
            symbol: Trading symbol

        Returns:
            Dict with metadata, schema, and sample rows
        """
        # Get first file from index
        if symbol not in self._tick_index.index:
            return {
                'error': f"Symbol {symbol} not found in tick index",
                'symbol': symbol
            }

        files = self._tick_index.index[symbol]
        first_file = Path(files[0]['path'])

        # Load parquet metadata
        pq_file = pq.ParquetFile(first_file)
        metadata = self._extract_parquet_metadata(pq_file)

        # Load schema
        schema = self._extract_schema(pq_file)

        # Load first 10 rows
        df = pd.read_parquet(first_file)
        sample_rows = df.head(10)

        # File statistics
        stats = {
            'file_name': first_file.name,
            'file_size_mb': round(first_file.stat().st_size / (1024 * 1024), 2),
            'total_ticks': len(df),
            'num_row_groups': pq_file.num_row_groups,
            'columns': list(df.columns),
            'start_time': df['timestamp'].min().isoformat() if 'timestamp' in df else None,
            'end_time': df['timestamp'].max().isoformat() if 'timestamp' in df else None,
        }

        return {
            'symbol': symbol,
            'data_type': 'ticks',
            'metadata': metadata,
            'schema': schema,
            'sample_rows': sample_rows,
            'stats': stats
        }

    def inspect_bars(self, symbol: str, timeframe: str) -> Dict[str, Any]:
        """
        Inspect bar data for a symbol and timeframe.

        Args:
            symbol: Trading symbol
            timeframe: Timeframe (e.g., 'M5')

        Returns:
            Dict with metadata, schema, and sample rows
        """
        if not self._bar_index:
            return {
                'error': 'Bar index manager not provided',
                'symbol': symbol,
                'timeframe': timeframe
            }

        # Get bar file from index
        bar_file = self._bar_index.get_bar_file(symbol, timeframe)

        if not bar_file:
            return {
                'error': f"Bar file not found for {symbol} {timeframe}",
                'symbol': symbol,
                'timeframe': timeframe
            }

        # Load parquet metadata
        pq_file = pq.ParquetFile(bar_file)
        metadata = self._extract_parquet_metadata(pq_file)

        # Load schema
        schema = self._extract_schema(pq_file)

        # Load first 10 rows
        df = pd.read_parquet(bar_file)
        sample_rows = df.head(10)

        # File statistics
        stats = {
            'file_name': bar_file.name,
            'file_size_mb': round(bar_file.stat().st_size / (1024 * 1024), 2),
            'total_bars': len(df),
            'num_row_groups': pq_file.num_row_groups,
            'columns': list(df.columns),
            'start_time': df['timestamp'].min().isoformat() if 'timestamp' in df else None,
            'end_time': df['timestamp'].max().isoformat() if 'timestamp' in df else None,
        }

        return {
            'symbol': symbol,
            'timeframe': timeframe,
            'data_type': 'bars',
            'metadata': metadata,
            'schema': schema,
            'sample_rows': sample_rows,
            'stats': stats
        }

    def _extract_parquet_metadata(self, pq_file: pq.ParquetFile) -> Dict[str, str]:
        """
        Extract all metadata fields from parquet file.

        Args:
            pq_file: ParquetFile instance

        Returns:
            Dict with all metadata key-value pairs
        """
        metadata_raw = pq_file.metadata.metadata

        # Decode bytes to strings
        metadata = {}
        for key, value in metadata_raw.items():
            key_str = key.decode(
                'utf-8') if isinstance(key, bytes) else str(key)
            value_str = value.decode(
                'utf-8') if isinstance(value, bytes) else str(value)
            metadata[key_str] = value_str

        return metadata

    def _extract_schema(self, pq_file: pq.ParquetFile) -> Dict[str, str]:
        """
        Extract schema information from parquet file.

        Args:
            pq_file: ParquetFile instance

        Returns:
            Dict with column names and data types
        """
        schema = {}
        arrow_schema = pq_file.schema.to_arrow_schema()

        for field in arrow_schema:
            schema[field.name] = str(field.type)

        return schema

    def print_inspection(self, result: Dict[str, Any]):
        """
        Print inspection results in formatted output.

        Args:
            result: Inspection result dict
        """
        if 'error' in result:
            vLog.error(f"\n❌ {result['error']}")
            return

        # Header
        vLog.info("\n" + "=" * 80)
        if result['data_type'] == 'ticks':
            vLog.info(f"🔍 TICK DATA INSPECTION: {result['symbol']}")
        else:
            vLog.info(
                f"🔍 BAR DATA INSPECTION: {result['symbol']} {result['timeframe']}")
        vLog.info("=" * 80)

        # File statistics
        stats = result['stats']
        vLog.info(f"\n📁 File Information:")
        vLog.info(f"   File:       {stats['file_name']}")
        vLog.info(f"   Size:       {stats['file_size_mb']:.2f} MB")
        if result['data_type'] == 'ticks':
            vLog.info(f"   Ticks:      {stats['total_ticks']:,}")
        else:
            vLog.info(f"   Bars:       {stats['total_bars']:,}")
        vLog.info(f"   Row Groups: {stats['num_row_groups']}")
        vLog.info(
            f"   Time Range: {stats['start_time']} → {stats['end_time']}")

        # Parquet metadata
        vLog.info(f"\n📋 Parquet Metadata:")
        metadata = result['metadata']
        for key, value in sorted(metadata.items()):
            if key in self.SKIP_KEYS:
                continue
            vLog.info(f"   {key:30s} = {value}")

        # Schema
        vLog.info(f"\n🔧 Schema:")
        schema = result['schema']
        for col_name, col_type in schema.items():
            vLog.info(f"   {col_name:30s} : {col_type}")

        # Sample rows
        vLog.info(f"\n📊 Sample Data (first 10 rows):")
        sample_df = result['sample_rows']

        # Format DataFrame for display
        pd.set_option('display.max_columns', None)
        pd.set_option('display.width', None)
        pd.set_option('display.max_colwidth', 30)

        vLog.info("\n" + sample_df.to_string())

        vLog.info("\n" + "=" * 80 + "\n")
