"""
FiniexTestingIDE - API Authentication Config Types

The consumer token as this API gates it: the shared model, narrowed to the surfaces this
project actually serves.
"""

from typing import ClassVar, Tuple

from finiex_auth.consumer_token_base import ConsumerTokenBase
from pydantic import BaseModel


class ApiAuthConfig(BaseModel):
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


class ConsumerToken(ConsumerTokenBase):
    """
    One API consumer's credential, validated against this project's own surfaces.

    A surface is a ROUTER, not a route: `bars`, `brokers`, `reports`, `sweeps`. What a grant
    names is the thing a route addresses — its first path parameter — so `bars:kraken_spot` is
    one venue's bar data, while `reports:*` is every run report, because a run id is generated
    and nobody would write one into a token.

    The vocabulary is CLOSED on purpose. A grant naming anything else fails when the
    credentials file is parsed, at boot, instead of becoming a denial at request time that
    nobody can explain.
    """

    GRANT_SURFACES: ClassVar[Tuple[str, ...]] = ('bars', 'brokers', 'reports', 'sweeps')
