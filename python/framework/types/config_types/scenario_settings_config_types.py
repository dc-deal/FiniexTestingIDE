"""
FiniexTestingIDE - AutoTrader Scenario-Settings Config Schema (#438)

The AutoTrader-mock's single-scenario data + account description. Mirrors a simulation
scenario's core fields so a mock session replays scenario base data through the live decision
path: the index-resolved data window PLUS the account balances the run starts from. Fed into a
SingleScenario and prepared through the shared MountPreparer — the same index/validation stack
the backtesting batch uses.
"""

from typing import Any, Dict, Optional

from pydantic import BaseModel, ConfigDict, Field


class ScenarioSettingsConfig(BaseModel):
    """
    Scenario data + account description for an AutoTrader-mock session.

    Field names mirror the simulation scenario scalars (data_broker_type, data_sentiment_type,
    start/end_date, max_ticks, data_mode) so there is one vocabulary across both pipelines. The
    balances live here too (the sim carries them on the scenario's trade_simulator_config), which
    is why the AutoTrader profile no longer needs a separate `account` block.

    Args:
        data_broker_type: Data-source broker for the tick/bar index. '' → the profile's execution broker_type
        data_sentiment_type: Signal archive pipeline_id (e.g. 'crypto_sentiment'); '' = no SIGNAL feed
        start_date: Window start (UTC ISO string), index-resolved
        end_date: Window end (UTC ISO string); None with max_ticks = tick-limited mode
        max_ticks: Cap the loaded ticks; None with end_date = timespan mode
        data_mode: Tick data mode (e.g. 'realistic')
        balances: Starting account balances (e.g. {'USD': 10000.0, 'BTC': 0.0})
        account_currency: Explicit account currency; None → derived from balances + symbol
        name: Optional scenario name; '' → derived from the profile name
        stress_test_config: Optional stress config (stale_data_stress signal plane etc.), mirrors the sim scenario
    """
    model_config = ConfigDict(extra='forbid')

    data_broker_type: str = ''
    data_sentiment_type: str = ''
    start_date: str
    end_date: Optional[str] = None
    max_ticks: Optional[int] = None
    data_mode: str = 'realistic'
    balances: Dict[str, float] = Field(default_factory=dict)
    account_currency: Optional[str] = None
    name: str = ''
    stress_test_config: Optional[Dict[str, Any]] = None
