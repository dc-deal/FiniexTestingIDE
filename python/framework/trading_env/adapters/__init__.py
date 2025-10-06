"""
FiniexTestingIDE - Broker Adapters Package
Broker-specific implementations of the trading interface

This package contains adapters for different broker types (MT5, Kraken, etc.)
that translate broker-specific behavior into a unified trading interface.
"""

from .base_adapter import IOrderCapabilities
from .mt5_adapter import MT5Adapter
from .kraken_adapter import KrakenAdapter, KRAKEN_ENABLED, create_kraken_dummy_config

__all__ = [
    'IOrderCapabilities',
    'MT5Adapter',
    'KrakenAdapter',
    'KRAKEN_ENABLED',
    'create_kraken_dummy_config',
]
