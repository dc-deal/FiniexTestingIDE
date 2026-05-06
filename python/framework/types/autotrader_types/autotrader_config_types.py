"""
FiniexTestingIDE - AutoTrader Configuration Types
Typed configuration for live AutoTrader sessions.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from python.framework.types.config_types.autotrader_defaults_config_types import (
    AutotraderExecutionDefaults,
    ClippingMonitorDefaults,
    DisplayDefaults,
    OrderGuardDefaults,
)


@dataclass
class TickSourceConfig:
    """
    Configuration for tick data source.

    Args:
        type: Tick source type ('mock' for parquet replay, 'kraken' for live WebSocket)
        parquet_path: Path to parquet tick data file (mock mode)
        max_ticks: Stop after N ticks (mock mode only). 0 = no limit (full file)
        tick_delay_ms: Artificial delay per tick in ms (mock replay only). 0 = full speed
        ws_url: WebSocket URL (kraken mode)
        reconnect_initial_delay_s: Initial reconnect backoff delay in seconds (kraken mode)
        reconnect_max_delay_s: Maximum reconnect backoff delay cap in seconds (kraken mode)
        heartbeat_interval_s: Heartbeat check interval in seconds (kraken mode)
        heartbeat_dead_s: Silence threshold to force reconnect in seconds (kraken mode)
    """
    type: str = 'mock'
    parquet_path: str = ''
    max_ticks: int = 0
    tick_delay_ms: int = 0
    # WebSocket fields (used when type='kraken')
    ws_url: str = 'wss://ws.kraken.com/v2'
    reconnect_initial_delay_s: float = 1.0
    reconnect_max_delay_s: float = 60.0
    heartbeat_interval_s: float = 30.0
    heartbeat_dead_s: float = 90.0


@dataclass
class AccountConfig:
    """
    Account configuration for AutoTrader session.

    Args:
        balances: Asset balances (e.g., {'USD': 10000.0} or {'USD': 50.0, 'ETH': 0.0})
        account_currency: Explicit account currency override. If omitted, derived
            from balances keys + symbol (quote_currency preferred).
    """
    balances: Dict[str, float] = field(default_factory=dict)
    account_currency: Optional[str] = None


@dataclass
class SafetyConfig:
    """
    Circuit breaker configuration for live trading.

    Soft stop: blocks new positions when triggered, existing positions run out normally.
    Both conditions are OR-combined — either alone triggers the block.

    Args:
        enabled: Master switch for safety checks
        min_balance: Block new positions if balance drops below this value (margin mode, account currency)
        min_equity: Block new positions if equity drops below this value (spot mode, account currency)
        max_drawdown_pct: Block new positions if session drawdown exceeds this % (balance for margin, equity for spot)
    """
    enabled: bool = False
    min_balance: float = 0.0
    min_equity: float = 0.0
    max_drawdown_pct: float = 0.0


@dataclass
class AutoTraderConfig:
    """
    Top-level configuration for FiniexAutoTrader live sessions.

    Loaded from configs/autotrader_profiles/<profile>.json.
    Own format — NOT scenario-set based (different lifecycle).

    Args:
        name: Session name (used for log directory, e.g., 'btcusd_mock')
        symbol: Trading symbol (e.g., 'BTCUSD')
        broker_type: Broker type identifier (e.g., 'kraken_spot')
        adapter_type: Adapter type ('mock' or 'live')
        strategy_config: Complete strategy configuration (workers + decision logic)
        account: Account configuration
        tick_source: Tick source configuration
        execution: Execution parameters
        clipping_monitor: Clipping monitor configuration
    """
    name: str = ''
    symbol: str = ''
    broker_type: str = ''
    adapter_type: str = 'mock'
    strategy_config: Dict[str, Any] = field(default_factory=dict)
    account: AccountConfig = field(default_factory=AccountConfig)
    tick_source: TickSourceConfig = field(default_factory=TickSourceConfig)
    execution: AutotraderExecutionDefaults = field(default_factory=AutotraderExecutionDefaults)
    clipping_monitor: ClippingMonitorDefaults = field(default_factory=ClippingMonitorDefaults)
    display: DisplayDefaults = field(default_factory=DisplayDefaults)
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    order_guard: OrderGuardDefaults = field(default_factory=OrderGuardDefaults)
    config_path: Optional[Path] = None
