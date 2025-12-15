"""
FiniexTestingIDE - Broker Config Factory
Factory for loading and serializing broker configurations

ARCHITECTURE:
- from_json(): Load from JSON file (Main Process, 1x per batch)
- to_serializable_dict(): Serialize for ProcessDataPackage (CoW-safe)
- from_serialized_dict(): Re-hydrate in subprocess (no file I/O)

PERFORMANCE:
- JSON loaded once in main process
- Serialized dict shared via CoW to all subprocesses
- Each subprocess re-hydrates adapter (fast, no file I/O)
"""

import json
from pathlib import Path
from typing import Dict, Any

from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.reporting.broker_info_renderer import BrokerInfoRenderer
from python.framework.trading_env.broker_config import BrokerConfig, BrokerType
from python.framework.trading_env.adapters.base_adapter import IOrderCapabilities
from python.framework.trading_env.adapters.mt5_adapter import MT5Adapter
from python.framework.trading_env.adapters.kraken_adapter import KrakenAdapter, KRAKEN_ENABLED
from python.framework.types.scenario_set_types import ScenarioSet, SingleScenario


class BrokerConfigFactory:
    """Factory for creating and serializing BrokerConfig instances."""

    @staticmethod
    def build_broker_config(config_path: str) -> BrokerConfig:
        """
        Load broker config from JSON file (Main Process only).

        Args:
            config_path: Path to broker JSON config

        Returns:
            BrokerConfig instance with adapter

        Raises:
            FileNotFoundError: If config file not found
            ValueError: If broker type unknown or unsupported
        """
        path = Path(config_path)

        if not path.exists():
            raise FileNotFoundError(f"Broker config not found: {config_path}")

        # Load JSON
        with open(path, 'r') as f:
            raw_config = json.load(f)

        # Detect broker type
        broker_type = BrokerConfig._detect_broker_type(raw_config, path)

        # Create adapter
        adapter = BrokerConfig._create_adapter(broker_type, raw_config)

        return BrokerConfig(broker_type, adapter)

    @staticmethod
    def to_serializable_dict(broker_config: BrokerConfig) -> Dict[str, Any]:
        """
        Serialize broker config to dict for ProcessDataPackage.

        Returns raw JSON dict - already CoW-safe and pickleable.
        Subprocess can re-hydrate adapter from this dict.

        Args:
            broker_config: BrokerConfig instance

        Returns:
            Serializable dict (original JSON data)
        """
        return broker_config.adapter.broker_config

    @staticmethod
    def from_serialized_dict(
        broker_type: BrokerType,
        config_dict: Dict[str, Any]
    ) -> BrokerConfig:
        """
        Re-hydrate broker config from serialized dict (Subprocess).

        No file I/O - adapter is created from dict in memory.
        Used in subprocesses to avoid redundant JSON loading.

        Args:
            config_dict: Serialized broker config dict
            config_path: Optional path for error messages

        Returns:
            BrokerConfig instance with fresh adapter

        Raises:
            ValueError: If broker type cannot be determined
        """

        # Create adapter from dict (no file I/O!)
        adapter = BrokerConfig._create_adapter(broker_type, config_dict)

        return BrokerConfig(broker_type, adapter)
