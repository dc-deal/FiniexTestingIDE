"""
FiniexTestingIDE - AutoTrader Defaults Configuration Types
Pydantic models for the app_config.json::autotrader section.

Note: This covers only the app_config defaults section — not the full
AutoTraderConfig profile type defined in autotrader_config_types.py.
"""
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
    price_threshold_pct: float = 1.0     # Looser — PRICE drift is structural on trade-channel data (#244)
    log_all: bool = False                # If True, log every event (not just threshold breaches)
    sample_rate: float = 1.0             # Reserved notausgang; V1.3 default = audit every fill


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
