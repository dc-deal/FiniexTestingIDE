"""
Broker-to-scenario mapping, held beside the preparator that builds it.

Its fields are runtime COLLABORATORS rather than data — `broker_config` is a built subsystem,
not a config section — so §6's exception applies and the unit lives beside its builder
(`broker_data_preparator.py`) instead of in `framework/types/`.

Keeping it in the type module made every importer of `scenario_set_types` pay for the whole
broker stack. Measured 2026-09-23: that module pulled 875 modules in 2230 ms against 195 in
614 ms without this class and its signal sibling, and the import instantiated the config
manager and opened the global log as a side effect.
"""

from dataclasses import dataclass
from typing import List, Set

from python.framework.trading_env.broker_config import BrokerConfig


@dataclass
class BrokerScenarioInfo:
    """Internal mapping of broker to scenarios (used for logging)."""
    config_path: str
    scenarios: List[str]
    symbols: Set[str]
    broker_config: BrokerConfig
