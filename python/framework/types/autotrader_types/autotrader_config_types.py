"""
FiniexTestingIDE - AutoTrader Configuration Types
Typed configuration for live AutoTrader sessions.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional


@dataclass
class TickSourceConfig:
    """
    Configuration for tick data source.

    Args:
        type: Tick source type ('mock' for parquet replay, 'kraken' for live WebSocket)
        parquet_path: Path to parquet tick data file (mock mode)
        mode: Replay mode ('replay' = fast, 'realtime' = time.sleep between ticks)
        max_ticks: Stop after N ticks (mock mode only). 0 = no limit (full file)
        ws_url: WebSocket URL (kraken mode)
        reconnect_initial_delay_s: Initial reconnect backoff delay in seconds (kraken mode)
        reconnect_max_delay_s: Maximum reconnect backoff delay cap in seconds (kraken mode)
        heartbeat_interval_s: Heartbeat check interval in seconds (kraken mode)
        heartbeat_dead_s: Silence threshold to force reconnect in seconds (kraken mode)
    """
    type: str = 'mock'
    parquet_path: str = ''
    mode: str = 'replay'
    max_ticks: int = 0
    # WebSocket fields (used when type='kraken')
    ws_url: str = 'wss://ws.kraken.com/v2'
    reconnect_initial_delay_s: float = 1.0
    reconnect_max_delay_s: float = 60.0
    heartbeat_interval_s: float = 30.0
    heartbeat_dead_s: float = 90.0


@dataclass
class ClippingMonitorConfig:
    """
    Configuration for live clipping monitoring (#197).

    Args:
        report_interval_s: Seconds between periodic clipping reports
        strategy: Clipping strategy ('queue_all' or 'drop_stale')
    """
    report_interval_s: float = 60.0
    strategy: str = 'queue_all'


@dataclass
class AccountConfig:
    """
    Account configuration for AutoTrader session.

    Args:
        initial_balance: Starting account balance
        currency: Account currency code (e.g., 'USD')
    """
    initial_balance: float = 10000.0
    currency: str = 'USD'


@dataclass
class SafetyConfig:
    """
    Circuit breaker configuration for live trading.

    Soft stop: blocks new positions when triggered, existing positions run out normally.
    Both conditions are OR-combined — either alone triggers the block.

    Args:
        enabled: Master switch for safety checks
        min_balance: Block new positions if balance drops below this value (account currency)
        max_drawdown_pct: Block new positions if session loss exceeds this % of initial balance
    """
    enabled: bool = False
    min_balance: float = 0.0
    max_drawdown_pct: float = 0.0


@dataclass
class DisplayConfig:
    """
    AutoTrader live console display configuration (#228).

    Args:
        enabled: Enable live console dashboard
        update_interval_ms: Display refresh interval in milliseconds
    """
    enabled: bool = True
    update_interval_ms: int = 300


@dataclass
class ExecutionConfig:
    """
    Execution configuration for AutoTrader session.

    Args:
        parallel_workers: Enable parallel worker execution
        bar_max_history: Maximum bar history size per timeframe
    """
    parallel_workers: bool = False
    bar_max_history: int = 1000


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
        broker_config_path: Path to broker config JSON
        adapter_type: Adapter type ('mock' or 'live')
        broker_settings: Broker settings filename (cascade: user_configs/broker_settings/ → configs/broker_settings/)
        strategy_config: Complete strategy configuration (workers + decision logic)
        account: Account configuration
        tick_source: Tick source configuration
        execution: Execution parameters
        clipping_monitor: Clipping monitor configuration
    """
    name: str = ''
    symbol: str = ''
    broker_type: str = ''
    broker_config_path: str = ''
    adapter_type: str = 'mock'
    broker_settings: str = ''
    strategy_config: Dict[str, Any] = field(default_factory=dict)
    account: AccountConfig = field(default_factory=AccountConfig)
    tick_source: TickSourceConfig = field(default_factory=TickSourceConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    clipping_monitor: ClippingMonitorConfig = field(default_factory=ClippingMonitorConfig)
    display: DisplayConfig = field(default_factory=DisplayConfig)
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    config_path: Optional[Path] = None
