"""
FiniexTestingIDE - AutoTrader Config Loader
Loads AutoTraderConfig from JSON file.
"""

import json
from pathlib import Path

from python.framework.types.autotrader_types.autotrader_config_types import (
    AccountConfig,
    AutoTraderConfig,
    ClippingMonitorConfig,
    ExecutionConfig,
    TickSourceConfig,
)


def load_autotrader_config(config_path: str) -> AutoTraderConfig:
    """
    Load AutoTraderConfig from JSON file.

    Args:
        config_path: Path to autotrader_config.json

    Returns:
        AutoTraderConfig instance
    """
    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"AutoTrader config not found: {config_path}")

    with open(path, 'r') as f:
        raw = json.load(f)

    # Parse nested config sections
    account_raw = raw.get('account', {})
    tick_source_raw = raw.get('tick_source', {})
    execution_raw = raw.get('execution', {})
    clipping_raw = raw.get('clipping_monitor', {})

    return AutoTraderConfig(
        name=raw.get('name', ''),
        symbol=raw.get('symbol', ''),
        broker_type=raw.get('broker_type', ''),
        broker_config_path=raw.get('broker_config_path', ''),
        adapter_type=raw.get('adapter_type', 'mock'),
        credentials_path=raw.get('credentials_path', None),
        strategy_config=raw.get('strategy_config', {}),
        account=AccountConfig(
            initial_balance=account_raw.get('initial_balance', 10000.0),
            currency=account_raw.get('currency', 'USD'),
        ),
        tick_source=TickSourceConfig(
            type=tick_source_raw.get('type', 'mock'),
            parquet_path=tick_source_raw.get('parquet_path', ''),
            mode=tick_source_raw.get('mode', 'replay'),
        ),
        execution=ExecutionConfig(
            parallel_workers=execution_raw.get('parallel_workers', False),
            bar_max_history=execution_raw.get('bar_max_history', 1000),
        ),
        clipping_monitor=ClippingMonitorConfig(
            report_interval_s=clipping_raw.get('report_interval_s', 60.0),
            strategy=clipping_raw.get('strategy', 'queue_all'),
        ),
    )
