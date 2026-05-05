"""
FiniexTestingIDE - AutoTrader Config Loader
Loads AutoTraderConfig from JSON file.
"""

import json
from pathlib import Path

from python.configuration.app_config_manager import AppConfigManager
from python.framework.utils.config_merge_utils import check_unknown_keys, deep_merge
from python.framework.types.autotrader_types.autotrader_config_types import (
    AccountConfig,
    AutoTraderConfig,
    ClippingMonitorConfig,
    DisplayConfig,
    ExecutionConfig,
    OrderGuardConfig,
    SafetyConfig,
    TickSourceConfig,
)

# ============================================
# Known config keys per profile section
# ============================================

_KNOWN_PROFILE_TOP_KEYS: frozenset = frozenset({
    'name', 'symbol', 'broker_type', 'adapter_type',
    'strategy_config', 'account', 'tick_source',
    'execution', 'clipping_monitor', 'display', 'safety', 'order_guard',
})
_KNOWN_EXECUTION_KEYS: frozenset = frozenset({'parallel_workers', 'bar_max_history'})
_KNOWN_CLIPPING_KEYS: frozenset  = frozenset({'report_interval_s', 'strategy'})
_KNOWN_DISPLAY_KEYS: frozenset   = frozenset({'enabled', 'update_interval_ms'})
_KNOWN_SAFETY_KEYS: frozenset    = frozenset({'enabled', 'min_balance', 'min_equity', 'max_drawdown_pct'})
_KNOWN_ORDER_GUARD_KEYS: frozenset = frozenset({'cooldown_seconds', 'max_consecutive_rejections'})
_KNOWN_ACCOUNT_KEYS: frozenset   = frozenset({'balances', 'account_currency'})
_KNOWN_TICK_SOURCE_KEYS: frozenset = frozenset({
    'type', 'parquet_path', 'max_ticks', 'tick_delay_ms',
    'ws_url', 'reconnect_initial_delay_s', 'reconnect_max_delay_s',
    'heartbeat_interval_s', 'heartbeat_dead_s',
})


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

    # Cascade: app_config.autotrader defaults → profile (profile wins)
    app_defaults = AppConfigManager().get_autotrader_defaults()
    if app_defaults:
        raw = deep_merge(app_defaults, raw, atomic_keys={'balances'})

    # Parse nested config sections
    account_raw = raw.get('account', {})
    tick_source_raw = raw.get('tick_source', {})
    execution_raw = raw.get('execution', {})
    clipping_raw = raw.get('clipping_monitor', {})
    display_raw = raw.get('display', {})
    safety_raw = raw.get('safety', {})
    order_guard_raw = raw.get('order_guard', {})

    # Structural key validation — profile level (pre-construction, full provenance)
    check_unknown_keys('profile (top level)', raw,              _KNOWN_PROFILE_TOP_KEYS)
    check_unknown_keys('execution',           execution_raw,    _KNOWN_EXECUTION_KEYS)
    check_unknown_keys('clipping_monitor',    clipping_raw,     _KNOWN_CLIPPING_KEYS)
    check_unknown_keys('display',             display_raw,      _KNOWN_DISPLAY_KEYS)
    check_unknown_keys('safety',              safety_raw,       _KNOWN_SAFETY_KEYS)
    check_unknown_keys('order_guard',         order_guard_raw,  _KNOWN_ORDER_GUARD_KEYS)
    check_unknown_keys('account',             account_raw,      _KNOWN_ACCOUNT_KEYS)
    check_unknown_keys('tick_source',         tick_source_raw,  _KNOWN_TICK_SOURCE_KEYS)

    return AutoTraderConfig(
        name=raw.get('name', ''),
        symbol=raw.get('symbol', ''),
        broker_type=raw.get('broker_type', ''),
        adapter_type=raw.get('adapter_type', 'mock'),
        strategy_config=raw.get('strategy_config', {}),
        account=AccountConfig(
            balances=account_raw.get('balances', {}),
            account_currency=account_raw.get('account_currency', None),
        ),
        tick_source=TickSourceConfig(
            type=tick_source_raw.get('type', 'mock'),
            parquet_path=tick_source_raw.get('parquet_path', ''),
            max_ticks=tick_source_raw.get('max_ticks', 0),
        ),
        execution=ExecutionConfig(
            parallel_workers=execution_raw.get('parallel_workers', False),
            bar_max_history=execution_raw.get('bar_max_history', 1000),
        ),
        clipping_monitor=ClippingMonitorConfig(
            report_interval_s=clipping_raw.get('report_interval_s', 60.0),
            strategy=clipping_raw.get('strategy', 'queue_all'),
        ),
        display=DisplayConfig(
            enabled=display_raw.get('enabled', True),
            update_interval_ms=display_raw.get('update_interval_ms', 300),
        ),
        safety=SafetyConfig(
            enabled=safety_raw.get('enabled', False),
            min_balance=safety_raw.get('min_balance', 0.0),
            min_equity=safety_raw.get('min_equity', 0.0),
            max_drawdown_pct=safety_raw.get('max_drawdown_pct', 0.0),
        ),
        order_guard=OrderGuardConfig(
            cooldown_seconds=order_guard_raw.get('cooldown_seconds', 60.0),
            max_consecutive_rejections=order_guard_raw.get('max_consecutive_rejections', 2),
        ),
        config_path=path,
    )
