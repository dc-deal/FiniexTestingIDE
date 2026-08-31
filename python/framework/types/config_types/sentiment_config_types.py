"""
FiniexTestingIDE - Sentiment Configuration Types
Pydantic models for sentiment_config.json (#141 Part 2a).

The signal side's mirror of market_config.json: that file holds market and broker facts
and a scenario points at them with broker_type; this one holds producer and pipeline
facts and a scenario points at them with data_sentiment_type. Both pipelines read it —
the per-source facts describe a SOURCE, not a run, so a simulation needs them as much as
a live session does.
"""
from dataclasses import dataclass
from typing import Dict, Optional

from pydantic import BaseModel, ConfigDict, Field

from python.framework.types.config_types.connection_policy_config_types import (
    ConnectionPolicy,
)
from python.framework.types.connection_types import GiveUpAction


@dataclass
class ActiveProducer:
    """
    The endpoint a run talks to, together with the credential that opens it.

    Resolved as one unit on purpose: the two always switch together, so handing them out
    separately is what would let an environment switch take effect by half.

    Args:
        name: Endpoint name from the registry (e.g. 'dev', 'production')
        base_url: Its address
        credential: The token that answered for it, with its source file
    """
    name: str
    base_url: str
    credential: 'ResolvedCredential'

    def describe(self) -> str:
        """
        Operator-readable one-liner, never containing the token (§29).

        Returns:
            Endpoint name, address and credential source
        """
        return (f'{self.name} ({self.base_url}) · credential '
                f'{self.credential.describe_source()}')


@dataclass
class ResolvedCredential:
    """
    A producer token together with the file that answered for it.

    Runtime result of a lookup, not a config schema — hence a dataclass beside the
    Pydantic models (§6). The source is the load-bearing half: with a tracked empty
    default and a gitignored override, "the token is configured" and "the token is
    empty and no header is sent" are otherwise indistinguishable from the log.
    """
    token: str
    source: Optional[str]

    def is_configured(self) -> bool:
        """
        Whether a non-empty token was found.

        Returns:
            True when a token will be sent as an Authorization header
        """
        return bool(self.token)

    def describe_source(self) -> str:
        """
        Operator-readable provenance, safe to log — never the token itself (§29).

        Returns:
            The answering file, or a statement that none did
        """
        if not self.source:
            return 'no credentials file found'
        return f'{self.source}' if self.token else f'{self.source} (empty)'


class SentimentSourceConfig(BaseModel):
    """
    Facts about one signal source, keyed by its pipeline_id.

    These belong to the source rather than to the run: two scenarios reading the same
    source must not be able to disagree about whether it observes weekends or how long
    its snapshots stay usable. A scenario may still override the staleness locally —
    that is the exception, not the home.
    """
    continuous: bool = True
    cadence_minutes: float = 10.0
    max_staleness_minutes: float = 30.0


class SentimentProducerEndpoint(BaseModel):
    """
    One reachable producer instance: where it is and which credential opens it.

    The two fields belong together and must switch together. A production token against
    a development address is a 401, and a 401 now stops the stream — so splitting them
    across two settings turns a one-word environment switch into a silent feed outage
    diagnosed at the wrong system.
    """
    model_config = ConfigDict(extra='forbid')

    base_url: str
    credentials_file: str = 'rag_credentials.json'


class SentimentProducerConfig(BaseModel):
    """
    The registered producer instances and which one is active.

    Switching environments is one word in the user override:
    `{"producer": {"active": "dev"}}`. An unknown name is a hard error rather than a
    fallback — a typo that silently keeps the previous endpoint is exactly the class of
    misconfiguration this block exists to remove.
    """
    model_config = ConfigDict(extra='forbid')

    active: str = 'dev'
    endpoints: Dict[str, SentimentProducerEndpoint] = Field(default_factory=dict)
    # How long one read of a producer route may take. It sits on the PRODUCER and not on
    # a transport because the readers that need it are transport-independent: the connect
    # check and the certificate observer run whether or not a session is streaming.
    request_timeout_s: float = 20.0

    def get_active_endpoint(self) -> SentimentProducerEndpoint:
        """
        The endpoint named by `active`.

        Returns:
            Its configuration
        """
        endpoint = self.endpoints.get(self.active)
        if endpoint is None:
            known = ', '.join(sorted(self.endpoints)) or '(none registered)'
            raise ValueError(
                f"sentiment_config.json: producer.active is '{self.active}', which is not "
                f'a registered endpoint. Known endpoints: {known}')
        return endpoint


class SentimentStreamConfig(BaseModel):
    """
    How to reach the producer's live stream and how to behave on its failures.

    Live-only. A simulation never opens a connection, and an AutoTrader mock session
    mounts its series from the archive instead, so this block is simply unused there.

    Unknown keys are REFUSED. Two fields left this block when the producer began serving
    them (`heartbeat_timeout_s`, `replay_window_hours`), and a workspace override still
    carrying one would otherwise be dropped in silence — the operator would be configuring
    a watchdog that no longer reads what they wrote.
    """
    model_config = ConfigDict(extra='forbid')

    enabled: bool = False
    pipeline_id: str = ''
    # A connection watchdog, never a freshness claim: the producer's keep-alive proves the
    # socket is alive, while a stalled seq proves the producer is not. A MULTIPLE rather
    # than a duration, because the interval it multiplies is served by the producer on
    # /v1/pipelines — a local copy of their number reports a feed outage that never
    # happened on the day they change it. The replay window is served the same way.
    heartbeat_timeout_multiple: float = 3.0
    # #473 — the stream's own reconnect ladder. Budget 0: a long-lived feed's whole job is
    # to come back, so it never gives up on a transport fault; the terminal control frames
    # and a refused credential are what end it, and those are classification, not budget.
    connection: ConnectionPolicy = ConnectionPolicy(
        initial_delay_s=5.0, max_delay_s=60.0, attempt_budget=0)
    # #473 — the registry read that PRECEDES the stream, and the one connection whose
    # give-up rule is a genuine operator choice: degrade is defensible here because the
    # staleness contracts (#434/#436) describe the reduced state and the boot bridge has
    # already mounted the archive slice, so the session starts STALE rather than blind.
    boot_connection: ConnectionPolicy = ConnectionPolicy(
        initial_delay_s=2.0, max_delay_s=30.0, attempt_budget=3,
        on_give_up=GiveUpAction.DEGRADE)


class SentimentHealthConfig(BaseModel):
    """
    How often the producer engine is asked who it is.

    Deliberately has no address of its own: the probe borrows the active transport's
    base_url, because the question it answers is "which journal am I consuming from",
    not "is some engine up". A third address to keep in sync could drift away from the
    one actually delivering envelopes, which is exactly the confusion this prevents.

    Cyclic rather than once at startup: the transport is a series of independent GETs
    against a static address, so nothing would notice if the producer were redeployed
    behind it. A journal change mid-session invalidates the cursor built against the
    previous one, so it must be seen rather than assumed away.
    """
    enabled: bool = True
    interval_s: float = 1800.0
    request_timeout_s: float = 10.0


class SentimentConfig(BaseModel):
    """
    Root of sentiment_config.json.

    Unknown keys are REFUSED for the same reason the stream block refuses them: the whole
    `poll` block left this file when the push transport took over, and a workspace
    override still carrying one would be dropped in silence — an operator would be
    enabling a transport that no longer exists and reading a healthy log for a session
    that never opened a connection.
    """
    model_config = ConfigDict(extra='forbid')

    producer: SentimentProducerConfig = Field(default_factory=SentimentProducerConfig)
    stream: SentimentStreamConfig = Field(default_factory=SentimentStreamConfig)
    health: SentimentHealthConfig = Field(default_factory=SentimentHealthConfig)
    sources: Dict[str, SentimentSourceConfig] = Field(default_factory=dict)

    def get_source(self, pipeline_id: str) -> Optional[SentimentSourceConfig]:
        """
        Facts registered for one signal source.

        Args:
            pipeline_id: The source's id (= a scenario's data_sentiment_type)

        Returns:
            Its configuration, or None when the source is not registered
        """
        return self.sources.get(pipeline_id)
