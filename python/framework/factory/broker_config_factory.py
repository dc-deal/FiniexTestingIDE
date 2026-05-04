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

import hashlib
import json
from pathlib import Path
from typing import Dict, Any

from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.batch_reporting.broker_info_renderer import BrokerInfoRenderer
from python.framework.trading_env.broker_config import BrokerConfig, BrokerType
from python.framework.trading_env.adapters.abstract_adapter import AbstractAdapter
from python.framework.trading_env.adapters.mt5_adapter import Mt5Adapter
from python.framework.trading_env.adapters.kraken_adapter import KrakenAdapter
from python.framework.types.scenario_types.scenario_set_types import ScenarioSet, SingleScenario


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

        BrokerConfigFactory._validate_symbol_integrity(raw_config, path)
        BrokerConfigFactory._inject_symbols_hash(raw_config)

        # Detect broker type
        broker_type = BrokerConfig._detect_broker_type(raw_config, path)

        # Create adapter
        adapter = BrokerConfig._create_adapter(broker_type, raw_config)

        return BrokerConfig(broker_type, adapter)

    @staticmethod
    def build_from_dict(config_dict: Dict[str, Any], source: str = '<dict>') -> BrokerConfig:
        """
        Build BrokerConfig from an in-memory dict.

        Validates symbol integrity and injects config hash — same as build_broker_config()
        but without file I/O. Used for dynamic broker configs loaded from the runtime cache.

        Args:
            config_dict: Raw broker config dict
            source: Description for error messages (e.g., cache file path)

        Returns:
            Validated BrokerConfig
        """
        source_path = Path(source)
        BrokerConfigFactory._validate_symbol_integrity(config_dict, source_path)
        BrokerConfigFactory._inject_symbols_hash(config_dict)
        broker_type = BrokerConfig._detect_broker_type(config_dict, source_path)
        adapter = BrokerConfig._create_adapter(broker_type, config_dict)
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

        BrokerConfigFactory._inject_symbols_hash(config_dict)

        # Create adapter from dict (no file I/O!)
        adapter = BrokerConfig._create_adapter(broker_type, config_dict)

        return BrokerConfig(broker_type, adapter)

    @staticmethod
    def _validate_symbol_integrity(raw_config: Dict[str, Any], source_path: Path) -> None:
        """
        Validate base_currency + quote_currency match the symbol key in each entry.

        Args:
            raw_config: Raw broker config dict
            source_path: Config file path (for error messages)
        """
        known_quotes = ['USD', 'EUR', 'GBP', 'CAD', 'JPY', 'AUD']
        for symbol, spec in raw_config.get('symbols', {}).items():
            base = spec.get('base_currency', '')
            quote = spec.get('quote_currency', '')
            if not base or not quote:
                continue
            sym_upper = symbol.upper()
            expected_quote = next(
                (q for q in known_quotes if sym_upper.endswith(q)), sym_upper[-3:]
            )
            expected_base = sym_upper[:-len(expected_quote)]
            if base.upper() != expected_base or quote.upper() != expected_quote:
                raise ValueError(
                    f"❌ Broker config integrity error in '{source_path}':\n"
                    f"   Symbol '{symbol}' — base_currency '{base}' / quote_currency '{quote}'"
                    f" does not match symbol key.\n"
                    f"   Expected: base='{expected_base}', quote='{expected_quote}'\n"
                    f"   Fix: Correct the entry in the broker config JSON file."
                )

    @staticmethod
    def _inject_symbols_hash(config_dict: Dict[str, Any]) -> None:
        """
        Compute 8-char SHA256 hash of the symbols block and inject into _config_meta.

        Hash is computed from symbols only — _config_meta changes do not affect it.
        Modifies config_dict in-place.

        Args:
            config_dict: Broker config dict to update
        """
        symbols = config_dict.get('symbols', {})
        symbols_hash = hashlib.sha256(
            json.dumps(symbols, sort_keys=True).encode()
        ).hexdigest()[:8]
        if '_config_meta' not in config_dict:
            config_dict['_config_meta'] = {}
        config_dict['_config_meta']['symbols_hash'] = symbols_hash
