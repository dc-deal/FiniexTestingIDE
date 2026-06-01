"""
FiniexTestingIDE - AutoTrader Defaults Configuration Types
Pydantic models for the app_config.json::autotrader section.

Note: This covers only the app_config defaults section — not the full
AutoTraderConfig profile type defined in autotrader_config_types.py.
"""
from typing import Literal

from pydantic import BaseModel

from python.framework.types.config_types.performance_tracking_config_types import AutoTraderPerformanceTrackingConfig


class AutotraderExecutionDefaults(BaseModel):
    """AutoTrader tick-loop execution defaults."""
    parallel_workers: bool = False
    bar_max_history: int = 1000
    performance_tracking: AutoTraderPerformanceTrackingConfig = AutoTraderPerformanceTrackingConfig()


class ClippingMonitorDefaults(BaseModel):
    """Clipping monitor defaults."""
    report_interval_s: float = 60.0
    strategy: str = 'queue_all'


class DisplayDefaults(BaseModel):
    """Live console dashboard defaults."""
    enabled: bool = True
    update_interval_ms: int = 300


class OrderGuardDefaults(BaseModel):
    """Order guard pre-validation defaults."""
    cooldown_seconds: float = 60.0
    max_consecutive_rejections: int = 2


class DriftAuditConfig(BaseModel):
    """
    Read-only drift telemetry defaults (#327).

    Compares locally-computed fee/volume/price against broker-reported truth
    via the #326 trades-query pipeline. Logs drift events above thresholds.
    Does not mutate state — purely observational. Correction is #151.
    """
    enabled: bool = True
    fee_threshold_pct: float = 0.5       # Bug-signal threshold for fee drift
    volume_threshold_pct: float = 0.1    # Partial-fill signal
    price_threshold_pct: float = 1.0     # Kraken-intra-reporting consistency (QueryOrder vs QueryTrades)
    slippage_threshold_pct: float = 0.5  # Submission tick mid vs broker fill price (#340)
    log_all: bool = False                # If True, log every event (not just threshold breaches)
    sample_rate: float = 1.0             # Reserved notausgang; V1.3 default = audit every fill


class ReconciliationDefaults(BaseModel):
    """
    Live reconciliation defaults (#151) — broker truth-pull cadence + mode.

    Detects divergence between local shadow state and broker truth. ALERT_ONLY
    only in V1.3 (detect + log + SESSION counter, no mutation); AUTO_CORRECT /
    HALT_TRADING land in #349. Live-only; default disabled.
    """
    enabled: bool = False
    mode: Literal['alert_only', 'auto_correct', 'halt_trading'] = 'alert_only'
    interval_ticks: int = 100          # reconcile every N ticks ...
    min_interval_seconds: float = 60.0  # ... OR every M wall-clock seconds (hybrid)


class ApiMonitorConfig(BaseModel):
    """
    Broker REST transport-latency monitor defaults (#351).

    Per-endpoint latency + error/reject telemetry, own live panel, plus logging
    of the abnormal (failed calls + calls slower than slow_call_threshold_ms).
    Live-only; default ON for live (mock auto-disabled in the loader).
    """
    enabled: bool = True
    slow_call_threshold_ms: float = 3000.0  # calls slower than this are logged + flagged


class AutotraderDefaultsConfig(BaseModel):
    """
    Top-level model for app_config.json::autotrader.
    Provides global defaults merged into every AutoTrader profile at load time.
    """
    execution: AutotraderExecutionDefaults = AutotraderExecutionDefaults()
    clipping_monitor: ClippingMonitorDefaults = ClippingMonitorDefaults()
    display: DisplayDefaults = DisplayDefaults()
    order_guard: OrderGuardDefaults = OrderGuardDefaults()
    drift_audit: DriftAuditConfig = DriftAuditConfig()
    reconciliation: ReconciliationDefaults = ReconciliationDefaults()
    api_monitor: ApiMonitorConfig = ApiMonitorConfig()
