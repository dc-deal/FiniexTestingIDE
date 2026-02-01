"""
FiniexTestingIDE - Data Integration Test Fixtures
=================================================

Shared fixtures for data integration tests.
Validates data pipeline integrity from tick import through bar rendering.

Provides:
- BarsIndexManager (session-scoped)
- MarketConfigManager (session-scoped)
- Index data extraction helpers
"""

import pytest
from pathlib import Path
from typing import Dict, List, Any

import pandas as pd

from python.data_management.index.bars_index_manager import BarsIndexManager
from python.configuration.market_config_manager import MarketConfigManager


# =============================================================================
# CORE MANAGER FIXTURES (Session Scope)
# =============================================================================

@pytest.fixture(scope="session")
def bars_index_manager() -> BarsIndexManager:
    """
    Initialize BarsIndexManager and load index.
    
    Session-scoped: Index loaded once, shared across all tests.
    
    Returns:
        BarsIndexManager with loaded index
    """
    manager = BarsIndexManager()
    manager.build_index(force_rebuild=False)
    return manager


@pytest.fixture(scope="session")
def market_config() -> MarketConfigManager:
    """
    Initialize MarketConfigManager.
    
    Single Source of Truth for broker_type → market_type mapping.
    
    Returns:
        MarketConfigManager instance
    """
    return MarketConfigManager()


# =============================================================================
# INDEX DATA FIXTURES
# =============================================================================

@pytest.fixture(scope="session")
def available_broker_types(bars_index_manager: BarsIndexManager) -> List[str]:
    """
    Get list of available broker types from index.
    
    Returns:
        List of broker_type strings (e.g., ['kraken_spot', 'mt5'])
    """
    return bars_index_manager.list_broker_types()


@pytest.fixture(scope="session")
def index_data(bars_index_manager: BarsIndexManager) -> Dict[str, Dict[str, Dict[str, Dict]]]:
    """
    Get raw index data structure.
    
    Structure: {broker_type: {symbol: {timeframe: entry}}}
    
    Returns:
        Complete index dictionary
    """
    return bars_index_manager.index


# =============================================================================
# HELPER FIXTURES
# =============================================================================

@pytest.fixture(scope="session")
def bar_file_loader(bars_index_manager: BarsIndexManager):
    """
    Factory fixture for loading bar DataFrames.
    
    Returns:
        Function to load bar file as DataFrame
    """
    def _load(broker_type: str, symbol: str, timeframe: str) -> pd.DataFrame:
        bar_file = bars_index_manager.get_bar_file(broker_type, symbol, timeframe)
        if bar_file is None:
            raise FileNotFoundError(
                f"Bar file not found: {broker_type}/{symbol}/{timeframe}"
            )
        return pd.read_parquet(bar_file)
    
    return _load


@pytest.fixture(scope="session")
def get_market_type(market_config: MarketConfigManager):
    """
    Factory fixture for market type lookup.
    
    Returns:
        Function to get market_type for broker_type
    """
    def _get(broker_type: str) -> str:
        return market_config.get_market_type(broker_type)
    
    return _get
