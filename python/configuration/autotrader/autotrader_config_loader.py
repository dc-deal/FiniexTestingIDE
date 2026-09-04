"""
FiniexTestingIDE - AutoTrader Config Loader
Loads AutoTraderConfig from JSON file.
"""

import dataclasses
import json
from pathlib import Path
from typing import Any, Dict

from python.configuration.app_config_manager import AppConfigManager
from python.framework.types.autotrader_types.autotrader_config_types import (
    AutoTraderConfig,
    SafetyConfig,
    TickSourceConfig,
)
from python.framework.types.config_types.autotrader_defaults_config_types import (
    ApiMonitorConfig,
    AutotraderExecutionDefaults,
    ClippingMonitorDefaults,
    ColdStartDefaults,
    DisplayDefaults,
    DriftAuditConfig,
    OrderGuardDefaults,
    ReconciliationDefaults,
    SessionEndDefaults,
    StatePersistenceDefaults,
)
from python.framework.types.config_types.performance_tracking_config_types import (
    AutoTraderPerformanceTrackingConfig,
)
from python.framework.types.config_types.scenario_settings_config_types import (
    ScenarioSettingsConfig,
)
from python.framework.utils.config_merge_utils import (
    check_unknown_keys,
    deep_merge,
    without_meta_keys,
)

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
_KNOWN_STATE_PERSISTENCE_KEYS: frozenset    = _allowlist_from(StatePersistenceDefaults)
_KNOWN_COLD_START_KEYS: frozenset           = _allowlist_from(ColdStartDefaults)
_KNOWN_SESSION_END_KEYS: frozenset          = _allowlist_from(SessionEndDefaults)
_KNOWN_PERFORMANCE_TRACKING_KEYS: frozenset = _allowlist_from(AutoTraderPerformanceTrackingConfig)
_KNOWN_TICK_SOURCE_KEYS: frozenset          = _allowlist_from(TickSourceConfig)
_KNOWN_SCENARIO_SETTINGS_KEYS: frozenset    = _allowlist_from(ScenarioSettingsConfig)


def _block(raw: Dict[str, Any], **overrides: Any) -> Dict[str, Any]:
    """
    The keyword arguments for one config block, built from its raw section.

    Two things happen here that every block needs and no block should repeat. The
    documentation-only keys are dropped: `check_unknown_keys` deliberately ALLOWS `_comment`
    (it is how a config file explains itself), and the two blocks that are still dataclasses
    rather than Pydantic models would raise a bare TypeError naming an argument the operator
    never thinks of as one. And the values resolved before construction — the mock
    auto-disables — ride on top rather than forcing a second, denser call shape.

    Args:
        raw: The block's raw section from the merged profile
        overrides: Values decided by the loader, applied over the raw ones

    Returns:
        The keyword arguments, ready to splat into the block's model
    """
    return {**without_meta_keys(raw), **overrides}

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
        raise FileNotFoundError(f'AutoTrader config not found: {config_path}')

    with open(path, 'r') as f:
        raw_profile_only = json.load(f)

    # Capture pre-merge provenance signals — needed by the mock auto-disable
    # logic below. After deep_merge() injects app_config defaults, the merged
    # `raw` can no longer distinguish between "profile explicitly set X" and
    # "X came from app_config defaults". Resolve those checks here against
    # the untouched profile dict.
    profile_explicitly_set_drift_enabled = (
        'enabled' in raw_profile_only.get('drift_audit', {})
    )
    profile_explicitly_set_reconciliation_enabled = (
        'enabled' in raw_profile_only.get('reconciliation', {})
    )
    profile_explicitly_set_api_monitor_enabled = (
        'enabled' in raw_profile_only.get('api_monitor', {})
    )
    profile_explicitly_set_state_persistence_enabled = (
        'enabled' in raw_profile_only.get('state_persistence', {})
    )

    # Cascade: app_config.autotrader defaults → profile (profile wins)
    app_defaults = AppConfigManager().get_autotrader_defaults()
    if app_defaults:
        raw = deep_merge(app_defaults, raw_profile_only, atomic_keys={'balances'})
    else:
        raw = raw_profile_only

    # Parse nested config sections
    scenario_settings_raw = raw.get('scenario_settings', None)
    tick_source_raw = raw.get('tick_source', {})
    execution_raw = raw.get('execution', {})
    clipping_raw = raw.get('clipping_monitor', {})
    display_raw = raw.get('display', {})
    safety_raw = raw.get('safety', {})
    order_guard_raw = raw.get('order_guard', {})
    drift_audit_raw = raw.get('drift_audit', {})
    reconciliation_raw = raw.get('reconciliation', {})
    api_monitor_raw = raw.get('api_monitor', {})
    state_persistence_raw = raw.get('state_persistence', {})
    cold_start_raw = raw.get('cold_start', {})
    session_end_raw = raw.get('session_end', {})
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
    check_unknown_keys('state_persistence',   state_persistence_raw, _KNOWN_STATE_PERSISTENCE_KEYS)
    check_unknown_keys('cold_start',          cold_start_raw,   _KNOWN_COLD_START_KEYS)
    check_unknown_keys('session_end',         session_end_raw,  _KNOWN_SESSION_END_KEYS)
    check_unknown_keys('tick_source',         tick_source_raw,  _KNOWN_TICK_SOURCE_KEYS)
    if scenario_settings_raw is not None:
        check_unknown_keys('scenario_settings', scenario_settings_raw, _KNOWN_SCENARIO_SETTINGS_KEYS)

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
        reconciliation_enabled_resolved = reconciliation_raw.get('enabled', True)

    # API monitor: same mock-auto-disable rationale — a mock adapter has no real
    # _fetch_private transport, so the monitor would record nothing useful.
    if adapter_type_resolved == 'mock' and not profile_explicitly_set_api_monitor_enabled:
        api_monitor_enabled_resolved = False
    else:
        api_monitor_enabled_resolved = api_monitor_raw.get('enabled', True)

    # State persistence auto-disables for mock adapters too: a mock session is a
    # dress-rehearsal, not a real restart context, and would otherwise write a
    # state file for a test profile. Auto-disable for mock UNLESS the profile sets
    # `enabled` explicitly (same provenance pattern as drift_audit/reconciliation).
    if adapter_type_resolved == 'mock' and not profile_explicitly_set_state_persistence_enabled:
        state_persistence_enabled_resolved = False
    else:
        state_persistence_enabled_resolved = state_persistence_raw.get('enabled', True)

    return AutoTraderConfig(
        name=raw.get('name', ''),
        symbol=raw.get('symbol', ''),
        broker_type=raw.get('broker_type', ''),
        adapter_type=adapter_type_resolved,
        dry_run=raw.get('dry_run', None),
        strategy_config=raw.get('strategy_config', {}),
        scenario_settings=(
            ScenarioSettingsConfig(**scenario_settings_raw)
            if scenario_settings_raw is not None else None
        ),
        # Each block is built from its raw dict as a WHOLE, never field by field. Every
        # transcribed field used to carry a THIRD copy of its default (model, config file,
        # loader fallback) that §28 requires to agree with the other two and nothing
        # enforced — and two fields had no transcription at all, so a profile could set
        # them, pass the allowlist above, and be ignored: `book_drift_interval_ticks` and
        # `warn_above_ratio` were declared, mirrored, read at runtime and never loaded.
        # `_block(raw, **resolved)` cannot forget a field, and it is ONE shape for the
        # blocks that take their section unchanged and the four whose `enabled` the loader
        # decides above.
        tick_source=TickSourceConfig(**_block(tick_source_raw)),
        execution=AutotraderExecutionDefaults(**_block(execution_raw)),
        clipping_monitor=ClippingMonitorDefaults(**_block(clipping_raw)),
        display=DisplayDefaults(**_block(display_raw)),
        safety=SafetyConfig(**_block(safety_raw)),
        order_guard=OrderGuardDefaults(**_block(order_guard_raw)),
        drift_audit=DriftAuditConfig(**_block(drift_audit_raw, enabled=drift_audit_enabled_resolved)),
        reconciliation=ReconciliationDefaults(**_block(reconciliation_raw, enabled=reconciliation_enabled_resolved)),
        api_monitor=ApiMonitorConfig(**_block(api_monitor_raw, enabled=api_monitor_enabled_resolved)),
        cold_start=ColdStartDefaults(**_block(cold_start_raw)),
        session_end=SessionEndDefaults(**_block(session_end_raw)),
        state_persistence=StatePersistenceDefaults(**_block(state_persistence_raw, enabled=state_persistence_enabled_resolved)),
        config_path=path,
    )
