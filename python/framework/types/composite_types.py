"""
Composite types — a run's accounts grouped by what they ran: the key, its parts, a changed value (#615).
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Optional

from python.framework.types.api.report_types import OrdersTo, TicksFrom
from python.framework.types.config_types.composite_definition_config_types import CompositeTier


class CompositeDocument(StrEnum):
    """
    Which rendered document a composite key is projected from.

    SCENARIO: one scenario of a rendered scenario set — a backtest's account
    SESSION: a rendered AutoTrader profile — a session's account
    """
    SCENARIO = 'scenario'
    SESSION = 'session'


class CompositePart(StrEnum):
    """
    The part of the composite key a value feeds — recorded separately, so two runs can be compared
    on everything except one part.

    STRATEGY: the decision logic, its parameters, its workers and its signal source
    CONDITIONS: the venue model, latency, costs, cadence, the order guard, safety
    ACCOUNT: the resolved account currency and the account model
    RUN_AXES: which pipeline ran, where the ticks came from, where the orders went
    """
    STRATEGY = 'strategy'
    CONDITIONS = 'conditions'
    ACCOUNT = 'account'
    RUN_AXES = 'run_axes'


@dataclass(frozen=True)
class RunAxes:
    """
    The axes that tell a backtest, a mock session, a dry run and real money apart — the resolved
    answers the run header records, never the profile's own words.

    Args:
        run_type: Which pipeline ran the account (the run header's `run_type`)
        ticks_from: Where its ticks came from
        orders_to: Where its orders went; None for a session the dry-run rule refused
    """
    run_type: str
    ticks_from: TicksFrom
    orders_to: Optional[OrdersTo]


@dataclass(frozen=True)
class CompositeKey:
    """
    One account's composite key with its parts, as it is stamped on the run's records.

    Args:
        composite_key: The fingerprint over every part, the definition and the key version
        composite_strategy: The strategy part's fingerprint
        composite_conditions: The conditions part's fingerprint
        composite_account: The account part's fingerprint
        composite_run_axes: The run-axes part's fingerprint
        excluded_member: Whether the account is an excluded member — a stress test switched on
        composite_definition_fingerprint: The fingerprint of the definition section it was made under
        composite_key_version: The version of the projection that made it
    """
    composite_key: str
    composite_strategy: str
    composite_conditions: str
    composite_account: str
    composite_run_axes: str
    excluded_member: bool
    composite_definition_fingerprint: str
    composite_key_version: int


@dataclass(frozen=True)
class ConfigChange:
    """
    One value that differs between two rendered documents.

    Args:
        setting: The value's path in the rendered document (a session's without `blocks.`)
        before: Its value in the first document; None when the first does not hold it
        after: Its value in the second document; None when the second does not hold it
        composite_tier: The value's composite tier
        composite_part: The part of the key it feeds; None for a value outside the key
    """
    setting: str
    before: Any
    after: Any
    composite_tier: CompositeTier
    composite_part: Optional[CompositePart]
