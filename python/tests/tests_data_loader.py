"""
Tests for TickDataLoader
"""

import pytest
import pandas as pd
from pathlib import Path
import tempfile
from unittest.mock import Mock, patch

from python.data_loader import TickDataLoader


class TestTickDataLoader:

    def test_init_with_valid_directory(self):
        """Test initialization with valid directory"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            loader = TickDataLoader(tmp_dir)
            assert loader.data_dir == Path(tmp_dir)

    def test_init_with_invalid_directory(self):
        """Test initialization with invalid directory"""
        with pytest.raises(FileNotFoundError):
            TickDataLoader("/non/existent/directory")

    def test_list_available_symbols_empty_directory(self):
        """Test symbol listing with empty directory"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            loader = TickDataLoader(tmp_dir)
            symbols = loader.list_available_symbols()
            assert symbols == []

    def test_list_available_symbols_with_files(self):
        """Test symbol listing with parquet files"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            # Create mock parquet files
            (Path(tmp_dir) / "EURUSD_20240101_120000.parquet").touch()
            (Path(tmp_dir) / "GBPUSD_20240101_120000.parquet").touch()
            (Path(tmp_dir) / "EURUSD_20240102_120000.parquet").touch()

            loader = TickDataLoader(tmp_dir)
            symbols = loader.list_available_symbols()

            assert set(symbols) == {"EURUSD", "GBPUSD"}

    @patch("pandas.read_parquet")
    def test_load_symbol_data_success(self, mock_read_parquet):
        """Test successful symbol data loading"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            # Setup mock data
            mock_df = pd.DataFrame(
                {
                    "timestamp": pd.date_range("2024-01-01", periods=100, freq="1s"),
                    "bid": [1.0] * 100,
                    "ask": [1.001] * 100,
                }
            )
            mock_read_parquet.return_value = mock_df

            # Create mock file
            (Path(tmp_dir) / "EURUSD_20240101_120000.parquet").touch()

            loader = TickDataLoader(tmp_dir)
            result = loader.load_symbol_data("EURUSD")

            assert len(result) == 100
            assert "timestamp" in result.columns

    def test_load_symbol_data_no_files(self):
        """Test loading data for symbol with no files"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            loader = TickDataLoader(tmp_dir)

            with pytest.raises(ValueError, match="Keine Daten für Symbol"):
                loader.load_symbol_data("NONEXISTENT")

    def test_clear_cache(self):
        """Test cache clearing"""
        with tempfile.TemporaryDirectory() as tmp_dir:
            loader = TickDataLoader(tmp_dir)
            loader._symbol_cache["test"] = "data"

            loader.clear_cache()
            assert loader._symbol_cache == {}
