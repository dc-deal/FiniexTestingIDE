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
    SafetyConfig,
    SessionEndDefaults,
    StatePersistenceDefaults,
    TickSourceConfig,
)
from python.framework.types.config_types.operator_alerts_config_types import OperatorAlertsConfig
from python.framework.types.config_types.scenario_settings_config_types import (
    ScenarioSettingsConfig,
)
from python.framework.types.run_purpose_types import RunPurpose


@dataclass
class DeploymentConfig:
    """
    Whether this profile's sessions form ONE continuous deployment (#497).

    An ARMING declaration, in the same family as `dry_run` — and the reason it is MANDATORY
    rather than defaulted is that the family's usual safe default does not exist here. With
    `dry_run` the safe value is True: forgetting it prevents real orders. Here BOTH directions
    cost something, and the expensive one is caused BY a default:

    - forgotten on the deployed profile → the sessions never group, and the join key cannot be
      added afterwards. A month of history is unrecoverable
    - set wrongly on a one-off profile (a field study, an adapter certification) → unrelated
      runs are grouped. Annoying, and every figure is still there to be read separately

    So there is no value that is safe to inherit, and the loader refuses a profile that does not
    say. The cost is one line per profile and the gain is that every profile states what it is —
    which today cannot be read off one at all.

    Args:
        continuous: True = this bot's sessions are one deployment and their ledger rows join
            into one history. False = each start is its own run and names no parent. The CLI
            may only narrow this (`--one-off`); declaring a deployment from the command line
            is deliberately impossible, because an unattended restart re-executes a command
            nobody typed and the deployment would fragment at exactly the restarts it exists
            to span
    """
    continuous: bool


@dataclass
class AutoTraderConfig:
    """
    Top-level configuration for an AutoTrader session.

    Loaded from configs/autotrader_profiles/<purpose>/<profile>.json.
    Own format — NOT scenario-set based (different lifecycle).

    Args:
        profile_name: The profile's name — its run directory and its unit name in every report
            (e.g. 'btcusd_mock'). Required: a profile without one used to get a run directory
            named `<symbol>_<adapter>` and a unit name of its symbol, two answers to one question
        bot_id: The bot's DECLARED identity, and what its carry-over state is filed under
            (#538). Optional, and empty means the identity is composed from `profile_name` instead —
            which is what every profile did before this field existed. Declaring one matters
            when a profile is RENAMED: a display name is something an operator improves, and
            without a declared id the improvement points the bot at a new, empty document while
            the venue still holds its position. It survives every rename of everything else
        run_purpose: What this profile's sessions are FOR (#576) — the top-level `run_purpose`,
            absent meaning REGULAR. Every mock profile a test runs is a FIXTURE, the real-money
            field study a CERTIFICATE. Says what the session is, not how it trades, so neither
            fingerprint of the profile covers it
        carry_over_from: The `profile_name` of the profile whose carry-over this one CONTINUES —
            a later stage of the same bot, run strictly after it, never beside it (#358). Empty
            for every ordinary profile, which owns its carry-over alone. Only an acceptance
            study's or a test's profile may declare one, with the same `bot_id`, symbol, adapter,
            broker and carry-over directory as the profile it names. Says where the state comes
            from, not how the session trades, so neither fingerprint covers it
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
        operator_alerts: The out-of-band channels to the operator (#235) — Telegram alerts and
            the liveness ping, both off by default
        deployment: Whether this profile's sessions form one continuous deployment (#497).
            MANDATORY — the loader refuses a profile without it, because neither value is
            safe to inherit (see DeploymentConfig)
        dry_run: Optional per-profile dry-run override. None = use the broker's
            market_config default. A profile may only TIGHTEN the posture: True wins
            over a live broker default, while False against a dry-run broker default
            is refused at startup (DryRunConflictError). Enabling real orders stays a
            deliberate change to market_config.json, not something a copied profile does.
    """
    profile_name: str = ''
    bot_id: str = ''
    run_purpose: RunPurpose = RunPurpose.REGULAR
    carry_over_from: str = ''
    symbol: str = ''
    broker_type: str = ''
    adapter_type: str = 'mock'
    strategy_config: Dict[str, Any] = field(default_factory=dict)
    scenario_settings: Optional[ScenarioSettingsConfig] = None
    tick_source: TickSourceConfig = field(default_factory=TickSourceConfig)
    execution: AutotraderExecutionDefaults = field(default_factory=AutotraderExecutionDefaults)
    clipping_monitor: ClippingMonitorDefaults = field(default_factory=ClippingMonitorDefaults)
    # Defaulted to the SAFE reading only so the dataclass stays constructible in tests;
    # the loader refuses a real profile that does not declare it.
    deployment: DeploymentConfig = field(
        default_factory=lambda: DeploymentConfig(continuous=False))
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
    operator_alerts: OperatorAlertsConfig = field(default_factory=OperatorAlertsConfig)
    config_path: Optional[Path] = None
    dry_run: Optional[bool] = None

    def get_unit_name(self) -> str:
        """
        The name this session carries as a run unit — in every report row, the booking periods,
        the carry-over key and the provenance. One rule in one place, because a report consumer
        matches a session across sections by exactly this value.

        Returns:
            `profile_name`, else the symbol
        """
        return self.profile_name or self.symbol

    def get_data_broker_type(self) -> str:
        """
        The broker whose ticks this session reads — the key its archive and the bar routes are
        addressed by. A mock session may replay another broker's archive than the one it trades
        against; a venue session reads its own broker's feed.

        Returns:
            `scenario_settings.data_broker_type`, else `broker_type`
        """
        declared = self.scenario_settings.data_broker_type if self.scenario_settings else ''
        return AutoTraderConfig.resolve_data_broker_type(self.broker_type, declared)

    @staticmethod
    def resolve_data_broker_type(broker_type: str, declared: str) -> str:
        """
        The rule behind `get_data_broker_type`, for a reader that holds the RAW profile rather
        than the loaded model — the configuration directory reads files without the loader.

        Args:
            broker_type: The profile's execution broker
            declared: Its `scenario_settings.data_broker_type`, '' when absent

        Returns:
            `declared`, else `broker_type`
        """
        return declared or broker_type

    def get_data_sentiment_type(self) -> str:
        """
        The sentiment source this session's SIGNAL workers read. Declared by a mock session's
        scenario_settings only.

        Returns:
            `scenario_settings.data_sentiment_type`, else ''
        """
        if self.scenario_settings is None:
            return ''
        return self.scenario_settings.data_sentiment_type or ''
