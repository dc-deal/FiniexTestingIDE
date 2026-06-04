"""
FiniexTestingIDE - AutoTrader Config Loader
Loads AutoTraderConfig from JSON file.
"""

import dataclasses
import json
from pathlib import Path

from python.configuration.app_config_manager import AppConfigManager
from python.framework.utils.config_merge_utils import check_unknown_keys, deep_merge
from python.framework.types.autotrader_types.autotrader_config_types import (
    AccountConfig,
    AutoTraderConfig,
    SafetyConfig,
    TickSourceConfig,
)
from python.framework.types.config_types.autotrader_defaults_config_types import (
    ApiMonitorConfig,
    AutotraderExecutionDefaults,
    ClippingMonitorDefaults,
    DisplayDefaults,
    DriftAuditConfig,
    OrderGuardDefaults,
    ReconciliationDefaults,
)
from python.framework.types.config_types.performance_tracking_config_types import AutoTraderPerformanceTrackingConfig


# ============================================
# Known config keys per profile section
# ============================================
# Derived directly from the backing schema classes — single source of truth.
# Adding a field to a Pydantic model or @dataclass automatically extends the
# allowlist; no parallel hardcoded list to forget. Mixed support for both
# Pydantic BaseModel (model_fields) and @dataclass (dataclasses.fields).

def _allowlist_from(cls) -> frozenset:
    """Field-name allowlist derived from a Pydantic model or @dataclass."""
    if hasattr(cls, 'model_fields'):
        return frozenset(cls.model_fields.keys())
    return frozenset(f.name for f in dataclasses.fields(cls))


# Top-level keys include load-time meta (`config_path`) that must NOT appear
# in profile JSON. Filter that out so the allowlist matches the JSON surface.
_KNOWN_PROFILE_TOP_KEYS: frozenset = (
    _allowlist_from(AutoTraderConfig) - {'config_path'}
)
_KNOWN_EXECUTION_KEYS: frozenset            = _allowlist_from(AutotraderExecutionDefaults)
_KNOWN_CLIPPING_KEYS: frozenset             = _allowlist_from(ClippingMonitorDefaults)
_KNOWN_DISPLAY_KEYS: frozenset              = _allowlist_from(DisplayDefaults)
_KNOWN_SAFETY_KEYS: frozenset               = _allowlist_from(SafetyConfig)
_KNOWN_ORDER_GUARD_KEYS: frozenset          = _allowlist_from(OrderGuardDefaults)
_KNOWN_DRIFT_AUDIT_KEYS: frozenset          = _allowlist_from(DriftAuditConfig)
_KNOWN_RECONCILIATION_KEYS: frozenset       = _allowlist_from(ReconciliationDefaults)
_KNOWN_API_MONITOR_KEYS: frozenset          = _allowlist_from(ApiMonitorConfig)
_KNOWN_PERFORMANCE_TRACKING_KEYS: frozenset = _allowlist_from(AutoTraderPerformanceTrackingConfig)
_KNOWN_ACCOUNT_KEYS: frozenset              = _allowlist_from(AccountConfig)
_KNOWN_TICK_SOURCE_KEYS: frozenset          = _allowlist_from(TickSourceConfig)


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
        raw_profile_only = json.load(f)

    # Capture pre-merge provenance signals — needed by the mock auto-disable
    # logic below. After deep_merge() injects app_config defaults, the merged
    # `raw` can no longer distinguish between "profile explicitly set X" and
    # "X came from app_config defaults". Resolve those checks here against
    # the untouched profile dict.
    profile_adapter_type = raw_profile_only.get('adapter_type', 'mock')
    profile_explicitly_set_drift_enabled = (
        'enabled' in raw_profile_only.get('drift_audit', {})
    )
    profile_explicitly_set_reconciliation_enabled = (
        'enabled' in raw_profile_only.get('reconciliation', {})
    )
    profile_explicitly_set_api_monitor_enabled = (
        'enabled' in raw_profile_only.get('api_monitor', {})
    )

    # Cascade: app_config.autotrader defaults → profile (profile wins)
    app_defaults = AppConfigManager().get_autotrader_defaults()
    if app_defaults:
        raw = deep_merge(app_defaults, raw_profile_only, atomic_keys={'balances'})
    else:
        raw = raw_profile_only

    # Parse nested config sections
    account_raw = raw.get('account', {})
    tick_source_raw = raw.get('tick_source', {})
    execution_raw = raw.get('execution', {})
    clipping_raw = raw.get('clipping_monitor', {})
    display_raw = raw.get('display', {})
    safety_raw = raw.get('safety', {})
    order_guard_raw = raw.get('order_guard', {})
    drift_audit_raw = raw.get('drift_audit', {})
    reconciliation_raw = raw.get('reconciliation', {})
    api_monitor_raw = raw.get('api_monitor', {})
    performance_tracking_raw = execution_raw.get('performance_tracking', {})

    # Structural key validation — profile level (pre-construction, full provenance)
    check_unknown_keys('profile (top level)', raw,              _KNOWN_PROFILE_TOP_KEYS)
    check_unknown_keys('execution',           execution_raw,    _KNOWN_EXECUTION_KEYS)
    check_unknown_keys('execution.performance_tracking', performance_tracking_raw, _KNOWN_PERFORMANCE_TRACKING_KEYS)
    check_unknown_keys('clipping_monitor',    clipping_raw,     _KNOWN_CLIPPING_KEYS)
    check_unknown_keys('display',             display_raw,      _KNOWN_DISPLAY_KEYS)
    check_unknown_keys('safety',              safety_raw,       _KNOWN_SAFETY_KEYS)
    check_unknown_keys('order_guard',         order_guard_raw,  _KNOWN_ORDER_GUARD_KEYS)
    check_unknown_keys('drift_audit',         drift_audit_raw,  _KNOWN_DRIFT_AUDIT_KEYS)
    check_unknown_keys('reconciliation',      reconciliation_raw, _KNOWN_RECONCILIATION_KEYS)
    check_unknown_keys('api_monitor',         api_monitor_raw,  _KNOWN_API_MONITOR_KEYS)
    check_unknown_keys('account',             account_raw,      _KNOWN_ACCOUNT_KEYS)
    check_unknown_keys('tick_source',         tick_source_raw,  _KNOWN_TICK_SOURCE_KEYS)

    # Drift-audit default depends on adapter_type. Mock adapters produce
    # synthetic fee/volume figures that don't reflect any real broker — the
    # FEE-drift comparison would always raise huge deltas (noise, not
    # actionable). Auto-disable for mock UNLESS the profile sets `enabled`
    # explicitly. The provenance check uses the pre-merge profile dict
    # captured above — `raw['drift_audit']['enabled']` would always be `true`
    # post-merge because app_config.json sets the global default that way.
    adapter_type_resolved = raw.get('adapter_type', 'mock')
    if adapter_type_resolved == 'mock' and not profile_explicitly_set_drift_enabled:
        drift_audit_enabled_resolved = False
    else:
        drift_audit_enabled_resolved = drift_audit_raw.get('enabled', True)

    # Reconciliation auto-disables for mock adapters too: the MockBrokerAdapter
    # does not track submitted orders into its broker-truth state, so any resting
    # order would read as a false orphan. Auto-disable for mock UNLESS the profile
    # sets `enabled` explicitly (same provenance pattern as drift_audit). Live
    # adapters inherit the app_config default (enabled).
    if adapter_type_resolved == 'mock' and not profile_explicitly_set_reconciliation_enabled:
        reconciliation_enabled_resolved = False
    else:
        reconciliation_enabled_resolved = reconciliation_raw.get('enabled', False)

    # API monitor: same mock-auto-disable rationale — a mock adapter has no real
    # _fetch_private transport, so the monitor would record nothing useful.
    if adapter_type_resolved == 'mock' and not profile_explicitly_set_api_monitor_enabled:
        api_monitor_enabled_resolved = False
    else:
        api_monitor_enabled_resolved = api_monitor_raw.get('enabled', True)

    return AutoTraderConfig(
        name=raw.get('name', ''),
        symbol=raw.get('symbol', ''),
        broker_type=raw.get('broker_type', ''),
        adapter_type=adapter_type_resolved,
        dry_run=raw.get('dry_run', None),
        strategy_config=raw.get('strategy_config', {}),
        account=AccountConfig(
            balances=account_raw.get('balances', {}),
            account_currency=account_raw.get('account_currency', None),
        ),
        tick_source=TickSourceConfig(
            type=tick_source_raw.get('type', 'mock'),
            parquet_path=tick_source_raw.get('parquet_path', ''),
            max_ticks=tick_source_raw.get('max_ticks', 0),
            connection_check_interval_s=tick_source_raw.get('connection_check_interval_s', 30.0),
            connection_dead_s=tick_source_raw.get('connection_dead_s', 90.0),
        ),
        execution=AutotraderExecutionDefaults(
            parallel_workers=execution_raw.get('parallel_workers', False),
            bar_max_history=execution_raw.get('bar_max_history', 1000),
            heartbeat_interval_ms=execution_raw.get('heartbeat_interval_ms', 500),
            performance_tracking=AutoTraderPerformanceTrackingConfig(
                worker_decision_tracking=performance_tracking_raw.get('worker_decision_tracking', False),
            ),
        ),
        clipping_monitor=ClippingMonitorDefaults(
            report_interval_s=clipping_raw.get('report_interval_s', 60.0),
            strategy=clipping_raw.get('strategy', 'queue_all'),
        ),
        display=DisplayDefaults(
            enabled=display_raw.get('enabled', True),
            update_interval_ms=display_raw.get('update_interval_ms', 300),
        ),
        safety=SafetyConfig(
            enabled=safety_raw.get('enabled', False),
            min_balance=safety_raw.get('min_balance', 0.0),
            min_equity=safety_raw.get('min_equity', 0.0),
            max_drawdown_pct=safety_raw.get('max_drawdown_pct', 0.0),
        ),
        order_guard=OrderGuardDefaults(
            cooldown_seconds=order_guard_raw.get('cooldown_seconds', 60.0),
            max_consecutive_rejections=order_guard_raw.get('max_consecutive_rejections', 2),
        ),
        drift_audit=DriftAuditConfig(
            enabled=drift_audit_enabled_resolved,
            fee_threshold_pct=drift_audit_raw.get('fee_threshold_pct', 0.5),
            volume_threshold_pct=drift_audit_raw.get('volume_threshold_pct', 0.1),
            price_threshold_pct=drift_audit_raw.get('price_threshold_pct', 1.0),
            slippage_threshold_pct=drift_audit_raw.get('slippage_threshold_pct', 0.5),
            log_all=drift_audit_raw.get('log_all', False),
            sample_rate=drift_audit_raw.get('sample_rate', 1.0),
        ),
        reconciliation=ReconciliationDefaults(
            enabled=reconciliation_enabled_resolved,
            mode=reconciliation_raw.get('mode', 'alert_only'),
            interval_ticks=reconciliation_raw.get('interval_ticks', 100),
            min_interval_seconds=reconciliation_raw.get('min_interval_seconds', 60.0),
        ),
        api_monitor=ApiMonitorConfig(
            enabled=api_monitor_enabled_resolved,
            slow_call_threshold_ms=api_monitor_raw.get('slow_call_threshold_ms', 3000.0),
        ),
        config_path=path,
    )
