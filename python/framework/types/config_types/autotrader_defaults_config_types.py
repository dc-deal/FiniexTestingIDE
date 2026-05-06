"""
FiniexTestingIDE - AutoTrader Defaults Configuration Types
Pydantic models for the app_config.json::autotrader section.

Note: This covers only the app_config defaults section — not the full
AutoTraderConfig profile type defined in autotrader_config_types.py.
"""
from pydantic import BaseModel


class AutotraderExecutionDefaults(BaseModel):
    """AutoTrader tick-loop execution defaults."""
    parallel_workers: bool = False
    bar_max_history: int = 1000


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


class AutotraderDefaultsConfig(BaseModel):
    """
    Top-level model for app_config.json::autotrader.
    Provides global defaults merged into every AutoTrader profile at load time.
    """
    execution: AutotraderExecutionDefaults = AutotraderExecutionDefaults()
    clipping_monitor: ClippingMonitorDefaults = ClippingMonitorDefaults()
    display: DisplayDefaults = DisplayDefaults()
    order_guard: OrderGuardDefaults = OrderGuardDefaults()
