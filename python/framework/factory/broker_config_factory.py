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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional, Tuple

from python.framework.trading_env.broker_config import BrokerConfig, BrokerType
from python.framework.utils.time_utils import parse_datetime


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
            raise FileNotFoundError(f'Broker config not found: {config_path}')

        # Load JSON
        with open(path, 'r') as f:
            raw_config = json.load(f)

        BrokerConfigFactory._validate_symbol_integrity(raw_config, path)
        BrokerConfigFactory._inject_config_hashes(raw_config)

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
        BrokerConfigFactory._inject_config_hashes(config_dict)
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

        BrokerConfigFactory._inject_config_hashes(config_dict)

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
    def frozen_fee_age_days(
        config_path: str,
        now: Optional[datetime] = None,
    ) -> Optional[Tuple[str, int]]:
        """
        How old the declared fee structure's freeze is, when the file says (#505 follow-up).

        A fee rate is an ASSUMPTION, not a fetched fact: a volume-tiered venue prices per
        account, and the tier moves as the account trades. So the seed records WHEN it was
        frozen, and this reads that date back — the same shape a certificate's `valid_until`
        has, because it is the same kind of statement: a dated claim whose validity decays.

        Who needs it: a live session already compares the declared rate against the venue on
        every start and warns. Someone running only BACKTESTS never sees that warning, and
        the seed can rot indefinitely for them. This is the answer for that reader.

        A file with no freeze block returns None — an absence, not an age of zero. Reading
        `_fee_structure_frozen` rather than the fee block itself is deliberate: the block is
        inside `config_hash`, so a provenance note in it would move the reproducibility
        anchor without changing a price.

        Args:
            config_path: Path to a broker config JSON file
            now: The instant to measure against; current UTC when not given

        Returns:
            (freeze date as written, whole days since) — or None when the file declares none
        """
        with open(config_path, 'r', encoding='utf-8') as handle:
            raw = json.load(handle)
        stamped = raw.get('_fee_structure_frozen', {}).get('date')
        if not stamped:
            return None
        moment = now or datetime.now(timezone.utc)
        return stamped, (moment - parse_datetime(stamped)).days

    @staticmethod
    def fee_structure_from(config_path: str) -> Dict[str, Any]:
        """
        Read the fee structure a broker config file declares (#337).

        ONE declared source for both pipelines. The rate lives in the git-tracked seed and
        nowhere else: the backtest reads it so a run stays reproducible from a commit, and a
        live session starts from the same number so the two agree about what was expected
        before the venue is asked. Without this the live baseline came from a Python literal
        in the config fetcher — a third place the rate was written down, which no amount of
        re-freezing the seed could correct.

        Built through the normal factory path, so the file gets its usual validation rather
        than a second, looser read.

        Args:
            config_path: Path to a broker config JSON file

        Returns:
            The file's `fee_structure` block
        """
        config = BrokerConfigFactory.build_broker_config(config_path)
        fee_structure = config.adapter.broker_config.get('fee_structure')
        if not fee_structure:
            raise ValueError(
                f'❌ No fee structure declared in {config_path}. It is the one source both '
                f'pipelines read, so there is nothing to fall back to.'
            )
        return fee_structure

    @staticmethod
    def _inject_config_hashes(config_dict: Dict[str, Any]) -> None:
        """
        Compute the two identity hashes and inject them into _config_meta, in-place.

        TWO hashes, because they answer different questions and one of them was doing both
        jobs badly (#337):

        `symbols_hash` — the SYMBOL SET's identity. What the config fetcher and its CLI print,
        and what says whether a cache still describes the same instruments. Unchanged.

        `config_hash` — the REPRODUCIBILITY anchor, carried by the run report's broker section
        and from there to every surface it renders to. It covers everything that changes what
        a run PRODUCES, which is the symbols AND the fee structure: a fee rate moves realised
        P&L on every trade, so two runs with different rates are different runs. Reading
        `symbols_hash` for this made them indistinguishable. The run ledger and the
        certificates do not carry it yet (#510), so the cross-run record still cannot tell
        two differently-priced runs apart.

        Neither hash covers `_config_meta` itself, so injecting them is idempotent.

        Args:
            config_dict: Broker config dict to update
        """
        symbols = config_dict.get('symbols', {})
        symbols_hash = BrokerConfigFactory._hash_block(symbols)
        config_hash = BrokerConfigFactory._hash_block({
            'symbols': symbols,
            'fee_structure': config_dict.get('fee_structure', {}),
        })
        if '_config_meta' not in config_dict:
            config_dict['_config_meta'] = {}
        config_dict['_config_meta']['symbols_hash'] = symbols_hash
        config_dict['_config_meta']['config_hash'] = config_hash

    @staticmethod
    def _hash_block(block: Any) -> str:
        """
        8-char SHA256 over one config block, key-order independent.

        Args:
            block: Any JSON-serializable config fragment

        Returns:
            The first 8 hex characters of its SHA256
        """
        return hashlib.sha256(
            json.dumps(block, sort_keys=True).encode()
        ).hexdigest()[:8]
