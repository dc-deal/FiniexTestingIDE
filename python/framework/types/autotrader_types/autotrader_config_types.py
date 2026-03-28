"""
FiniexTestingIDE - AutoTrader Configuration Types
Typed configuration for live AutoTrader sessions.
"""

from dataclasses import dataclass, field
from typing import Any, Dict, Optional


@dataclass
class TickSourceConfig:
    """
    Configuration for tick data source.

    Args:
        type: Tick source type ('mock' for parquet replay)
        parquet_path: Path to parquet tick data file (mock mode)
        mode: Replay mode ('replay' = fast, 'realtime' = time.sleep between ticks)
    """
    type: str = 'mock'
    parquet_path: str = ''
    mode: str = 'replay'


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
        credentials_path: Credentials filename for live API access (cascade: user_configs/credentials/ → configs/credentials/)
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
    credentials_path: Optional[str] = None
    strategy_config: Dict[str, Any] = field(default_factory=dict)
    account: AccountConfig = field(default_factory=AccountConfig)
    tick_source: TickSourceConfig = field(default_factory=TickSourceConfig)
    execution: ExecutionConfig = field(default_factory=ExecutionConfig)
    clipping_monitor: ClippingMonitorConfig = field(default_factory=ClippingMonitorConfig)
