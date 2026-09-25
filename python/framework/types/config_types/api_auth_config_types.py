"""
FiniexTestingIDE - API Authentication Config Types

The consumer token as this API gates it: the shared model, narrowed to the surfaces this
project actually serves. And the account a token acts for (#551): a token says which CLIENT is
calling, an account says on whose BEHALF.
"""

from enum import StrEnum
from typing import ClassVar, Tuple

from finiex_auth.consumer_token_base import ConsumerTokenBase
from pydantic import field_validator

from python.framework.types.config_types.strict_config_model import StrictConfigModel
from python.framework.types.run_origin_types import OPERATOR_PERSON
from python.framework.utils.declared_id_utils import (
    DECLARED_ID_MAX_LENGTH,
    declared_id_malformed_reason,
)


class ApiAuthConfig(StrictConfigModel):
    """
    Whether the API requires a token, independent of whether any token is configured.

    The two are separate on purpose, and collapsing them broke the agreed rollout. Issuing a
    consumer's token and switching gating ON are different steps: the consumer needs its token
    BEFORE it can start sending the header, and it needs a window in which sending it is
    optional. Without this flag the first token written into the credentials file gates every
    route in the same instant — which is the step the browser client explicitly asked us not to
    take before it confirms.

    Default OFF, because the safe direction here is the one that cannot lock out a consumer who
    has not been told yet. It is a state to pass THROUGH, not to stay in: once every consumer
    confirms, this goes to true and the boot line stops saying routes are ungated.
    """

    require_auth: bool = False


def _checked_account_id(value: str, field: str) -> str:
    """
    Refuse an account id that is empty, malformed or the reserved console principal.

    Args:
        value: The declared account id
        field: The field name to report — `account_id` on an account, `account` on a token

    Returns:
        The value unchanged when it is usable
    """
    if not value:
        raise ValueError(f'`{field}` is empty — every account has an id')
    if value == OPERATOR_PERSON:
        # The console operator is a principal of its own. A token acting as it would let an API
        # client hold the one identity that may start real-money runs from a terminal.
        raise ValueError(
            f"`{field}: '{OPERATOR_PERSON}'` is reserved — it is the console operator, which is "
            f'a principal of its own and never an account a token can act for')
    reason = declared_id_malformed_reason(value)
    if reason is not None:
        raise ValueError(
            f"`{field}: '{value}'` {reason}. Allowed: 1 to {DECLARED_ID_MAX_LENGTH} characters "
            f'of a-z, 0-9 and hyphen — the same shape as a bot id')
    return value


class AccountKind(StrEnum):
    """
    Whether an account is a person or a machine.

    A sibling service calling this API is a SERVICE account, not a person with a service's name:
    a record that says a run was started for `ragengine` must not read as though somebody by that
    name asked for it.
    """
    PERSON = 'person'
    SERVICE = 'service'


class ApiAccount(StrictConfigModel):
    """
    One account an API consumer acts for (#551).

    Not a store yet: the file is written by the operator from a CLI block, never by the app. It
    becomes one the day the app creates accounts itself. The id follows the same shape rule as a
    bot id — through the one shared predicate, not a copy — and `operator` is reserved: the
    console is a principal of its own and no account can be it.

    Args:
        account_id: The key it is filed under — what a run records as its `person`
        kind: A person, or a machine consumer such as a sibling service
        display_name: How a human reads it in a report
        active: Kill switch — a switched-off account refuses the boot of every live token bound
            to it, so a token cannot outlive the account it acts for by accident
        note: One line on who or what this is
    """

    account_id: str
    kind: AccountKind
    display_name: str
    active: bool = True
    note: str = ''

    @field_validator('account_id')
    @classmethod
    def _account_id_is_well_formed(cls, value: str) -> str:
        """
        Refuse an id the account file cannot carry.

        Args:
            value: The declared id

        Returns:
            The id, when it is a usable shape and not the reserved principal
        """
        return _checked_account_id(value, 'account_id')


class ConsumerToken(ConsumerTokenBase):
    """
    One API consumer's credential, validated against this project's own surfaces.

    A surface is a ROUTER, not a route: `bars`, `brokers`, `deployments`, `reports`, `sweeps`.
    What a grant
    names is the thing a route addresses — its first path parameter — so `bars:kraken_spot` is
    one venue's bar data and `deployments:deploy_20260918_091413` is one bot's history, while
    `reports:*` is every run report, because a run id is generated and nobody would write one
    into a token.

    The vocabulary is CLOSED on purpose. A grant naming anything else fails when the
    credentials file is parsed, at boot, instead of becoming a denial at request time that
    nobody can explain.

    `account` is REQUIRED (#551), for the reason `grants` is: a token acting for nobody would
    have to default to somebody. Presenting the token IS acting as that account until a login
    exists; the boot refuses a live token whose account is missing or switched off.
    """

    GRANT_SURFACES: ClassVar[Tuple[str, ...]] = (
        'bars', 'brokers', 'deployments', 'reports', 'sweeps')

    account: str

    @field_validator('account')
    @classmethod
    def _account_is_well_formed(cls, value: str) -> str:
        """
        Refuse an account reference no account could answer to.

        Args:
            value: The account id the token names

        Returns:
            The id, when it is a usable shape and not the reserved principal
        """
        return _checked_account_id(value, 'account')
