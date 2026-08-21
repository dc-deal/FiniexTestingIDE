"""
FiniexTestingIDE - Sentiment Configuration Types
Pydantic models for sentiment_config.json (#141 Part 2a).

The signal side's mirror of market_config.json: that file holds market and broker facts
and a scenario points at them with broker_type; this one holds producer and pipeline
facts and a scenario points at them with data_sentiment_type. Both pipelines read it —
the per-source facts describe a SOURCE, not a run, so a simulation needs them as much as
a live session does.
"""
from typing import Dict, Optional

from pydantic import BaseModel, Field


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


class SentimentStreamConfig(BaseModel):
    """
    How to reach the producer's live stream and how to behave on its failures.

    Live-only. A simulation never opens a connection, and an AutoTrader mock session
    mounts its series from the archive instead, so this block is simply unused there.
    """
    enabled: bool = False
    base_url: str = 'http://host.docker.internal:8100'
    pipeline_id: str = ''
    credentials_file: str = 'rag_credentials.json'
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
    base_url: str = 'http://host.docker.internal:8100'
    pipeline_id: str = ''
    credentials_file: str = 'rag_credentials.json'
    interval_s: float = 60.0
    request_timeout_s: float = 20.0
    # Back-off applied when the producer reports it could not serve from its store. A GET
    # never spends on the producer side, but a store that cannot answer is a condition to
    # wait out rather than to hammer.
    degraded_backoff_s: float = 300.0


class SentimentConfig(BaseModel):
    """Root of sentiment_config.json."""
    stream: SentimentStreamConfig = Field(default_factory=SentimentStreamConfig)
    poll: SentimentPollConfig = Field(default_factory=SentimentPollConfig)
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
