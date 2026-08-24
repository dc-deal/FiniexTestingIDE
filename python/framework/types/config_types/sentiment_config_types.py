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
    a development address is a 401, and a 401 now stops the poll loop — so splitting them
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
    """
    enabled: bool = False
    pipeline_id: str = ''
    # Producer-side replay bound. Ours only needs to know it so a truncated recovery is
    # reported as such rather than mistaken for a short history.
    replay_window_hours: float = 24.0
    # A connection watchdog, never a freshness claim: the producer's keep-alive proves the
    # socket is alive, while a stalled seq proves the producer is not.
    heartbeat_timeout_s: float = 60.0
    reconnect_backoff_initial_s: float = 5.0
    reconnect_backoff_max_s: float = 60.0


class SentimentPollConfig(BaseModel):
    """
    The interim pull path, used while the producer's stream does not exist yet.

    Deliberately the throwaway half: a poll returns an envelope up to a full producer
    interval old (measured against the live engine: 101.8 s), which is precisely what the
    stream removes. Everything behind the inbox is the permanent path.
    """
    enabled: bool = False
    pipeline_id: str = ''
    interval_s: float = 60.0
    request_timeout_s: float = 20.0
    # Back-off applied when the producer reports it could not serve from its store. A GET
    # never spends on the producer side, but a store that cannot answer is a condition to
    # wait out rather than to hammer.
    degraded_backoff_s: float = 300.0


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
    """Root of sentiment_config.json."""
    producer: SentimentProducerConfig = Field(default_factory=SentimentProducerConfig)
    stream: SentimentStreamConfig = Field(default_factory=SentimentStreamConfig)
    poll: SentimentPollConfig = Field(default_factory=SentimentPollConfig)
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
