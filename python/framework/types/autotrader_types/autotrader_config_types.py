"""
FiniexTestingIDE - AutoTrader Configuration Types
Typed configuration for live AutoTrader sessions.
"""

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Optional

from python.framework.types.config_types.autotrader_defaults_config_types import (
    ApiMonitorConfig,
    AutotraderExecutionDefaults,
    CapitalDefaults,
    ClippingMonitorDefaults,
    ColdStartDefaults,
    DisplayDefaults,
    DriftAuditConfig,
    OrderGuardDefaults,
    ReconciliationDefaults,
    SessionEndDefaults,
    StatePersistenceDefaults,
)
from python.framework.types.config_types.scenario_settings_config_types import (
    ScenarioSettingsConfig,
)


@dataclass
class TickSourceConfig:
    """
    Configuration for the tick TRANSPORT (how ticks are delivered).

    Data description (broker/symbol/window) lives in `scenario_settings` (#438) — the mock replay
    resolves its ticks through the shared index/preparation stack, not from a raw file path.

    Args:
        type: Tick source type ('mock' for scenario-data replay, 'kraken' for live WebSocket)
        tick_delay_ms: Artificial delay per tick in ms (mock replay only). 0 = full speed
        ws_url: WebSocket URL (kraken mode)
        reconnect_initial_delay_s: Initial reconnect backoff delay in seconds (kraken mode)
        reconnect_max_delay_s: Maximum reconnect backoff delay cap in seconds (kraken mode)
        connection_check_interval_s: WS connection-liveness check interval in seconds (kraken mode)
        connection_dead_s: Silence threshold to force reconnect in seconds (kraken mode)
        freeze_after_ticks: Outage drill (#436, mock mode): pause emission once after N ticks. 0 = off
        freeze_duration_s: Outage drill (#436, mock mode): pause duration in wall seconds
    """
    type: str = 'mock'
    tick_delay_ms: int = 0
    # WebSocket fields (used when type='kraken')
    ws_url: str = 'wss://ws.kraken.com/v2'
    reconnect_initial_delay_s: float = 1.0
    reconnect_max_delay_s: float = 60.0
    connection_check_interval_s: float = 30.0
    connection_dead_s: float = 90.0
    # Outage drill (#436) — deliberate mid-replay feed silence (mock mode)
    freeze_after_ticks: int = 0
    freeze_duration_s: float = 0.0


@dataclass
class SafetyConfig:
    """
    Circuit breaker configuration for live trading.

    Soft stop: blocks new positions when triggered, existing positions run out normally.
    Both conditions are OR-combined — either alone triggers the block.

    Args:
        enabled: Master switch for safety checks
        min_balance: Block new positions if the ACCOUNT VALUE drops below this floor
            (margin mode, account currency). Since #356 both floors denominate the same
            quantity — `get_account_value()` — and the account model only decides which of
            the two names a profile writes. It used to be settled cash on margin, which
            moves only on realised P&L, so an open loss could not reach the floor at all
        min_equity: The same floor, spelled for spot mode (account currency)
        max_drawdown_pct: Block new positions if the session drawdown exceeds this % of the
            risk baseline. Measured on the ACCOUNT VALUE in both models since #356
        max_drawdown_abs: Block new positions if session drawdown exceeds this absolute
            amount (#314). The sibling of max_drawdown_pct, and the pair is the industry
            standard for a reason: a percentage auto-scales with the account, an absolute
            floor stops that percentage from becoming a dangerously large number on a large
            one. Independent — either can fire first
        max_daily_loss_abs: Block new positions if the loss since the day's start exceeds
            this absolute amount (#314). A DAILY limit guards a different failure from a
            session one: a session accumulates from process start, a day resets, and a bot
            that loses steadily every day never trips a session limit at all
        max_daily_loss_pct: The same daily limit as a percentage of the day-start value
        baseline_mode: Which denominator the drawdown is measured against (#356).
            'fixed' holds the value at deployment; 'high_water_mark' trails the peak ever
            seen and therefore RATCHETS — profit tightens the limit, which is the prop-firm
            convention and a deliberate choice rather than a default
        emergency_flatten_enabled: Master switch for the HARD stop (#356). Distinct from
            the soft block above, which only stops new entries: this one CLOSES what is
            open. Default false — turning it on changes what happens to real money
        max_drawdown_pct_hard: Drawdown %% at which everything is closed. 0.0 disables
        max_drawdown_abs_hard: Drawdown amount at which everything is closed. 0.0 disables
        spot_liquidate_to_quote: Whether the hard stop also SELLS a spot holding. Default
            false, and the asymmetry is real rather than timid: a margin position can lose
            more than the account holds, so liquidating it is the point; a spot holding
            cannot, so selling it converts an unrealised loss into a realised one and is a
            trading decision, not a safety one. Turn it on where the account must end flat
        persist_baseline: Whether the baseline survives a restart. Default true, because a
            baseline that does not is the drift this was built to remove: a restart
            mid-drawdown re-anchors at the drawn-down value and the accumulated loss is
            forgotten. False is the deliberate escape for a profile that wants a fresh
            reference on every start
    """
    enabled: bool = False
    min_balance: float = 0.0
    min_equity: float = 0.0
    max_drawdown_pct: float = 0.0
    max_drawdown_abs: float = 0.0
    max_daily_loss_abs: float = 0.0
    max_daily_loss_pct: float = 0.0
    baseline_mode: str = 'fixed'
    persist_baseline: bool = True
    emergency_flatten_enabled: bool = False
    max_drawdown_pct_hard: float = 0.0
    max_drawdown_abs_hard: float = 0.0
    spot_liquidate_to_quote: bool = False


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
        scenario_settings: Scenario data + account description (mock replay; #438). None for live.
        tick_source: Tick transport configuration
        execution: Execution parameters
        clipping_monitor: Clipping monitor configuration
        capital: What the bot may assume about the account it trades (#489) — whether the
            operator has DECLARED it exclusive, which is what makes account-level risk
            limits measure the bot's own denominator
        session_end: What the session does with resting orders and open positions when it
            ends (#492). Two axes; `orders: 'leave'` is the loosening one and needs the
            broker's posture behind it, the same way `dry_run` does
        dry_run: Optional per-profile dry-run override. None = use the broker's
            market_config default. A profile may only TIGHTEN the posture: True wins
            over a live broker default, while False against a dry-run broker default
            is refused at startup (DryRunConflictError). Enabling real orders stays a
            deliberate change to market_config.json, not something a copied profile does.
    """
    name: str = ''
    symbol: str = ''
    broker_type: str = ''
    adapter_type: str = 'mock'
    strategy_config: Dict[str, Any] = field(default_factory=dict)
    scenario_settings: Optional[ScenarioSettingsConfig] = None
    tick_source: TickSourceConfig = field(default_factory=TickSourceConfig)
    execution: AutotraderExecutionDefaults = field(default_factory=AutotraderExecutionDefaults)
    clipping_monitor: ClippingMonitorDefaults = field(default_factory=ClippingMonitorDefaults)
    display: DisplayDefaults = field(default_factory=DisplayDefaults)
    safety: SafetyConfig = field(default_factory=SafetyConfig)
    order_guard: OrderGuardDefaults = field(default_factory=OrderGuardDefaults)
    drift_audit: DriftAuditConfig = field(default_factory=DriftAuditConfig)
    reconciliation: ReconciliationDefaults = field(default_factory=ReconciliationDefaults)
    api_monitor: ApiMonitorConfig = field(default_factory=ApiMonitorConfig)
    state_persistence: StatePersistenceDefaults = field(default_factory=StatePersistenceDefaults)
    cold_start: ColdStartDefaults = field(default_factory=ColdStartDefaults)
    session_end: SessionEndDefaults = field(default_factory=SessionEndDefaults)
    capital: CapitalDefaults = field(default_factory=CapitalDefaults)
    config_path: Optional[Path] = None
    dry_run: Optional[bool] = None
