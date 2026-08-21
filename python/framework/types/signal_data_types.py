"""
FiniexTestingIDE - Signal Data Types
Typed schema for pre-collected external signal data (SIGNAL worker input, #141).

The producer emits one AnalysisEnvelope per run (all symbols); the collector archives
each as one JSONL line plus a `collected_msc` receive stamp (the no-look-ahead merge key).
Pydantic models validate the external schema on read; `extra='ignore'` keeps the reader
tolerant of producer-side metadata additions (only schema_version + the consumed result
fields are the strict contract).
"""

from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Tuple

from pydantic import (BaseModel, ConfigDict, Field, field_validator,
                      model_validator)


def _epoch_ms_to_utc(value):
    """
    Normalize an epoch-millisecond number to a UTC datetime.

    Args:
        value: Epoch-ms int/float, an ISO string, a datetime, or None

    Returns:
        A tz-aware UTC datetime for a number; the value unchanged otherwise
    """
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc)
    return value


class ArticleRef(BaseModel):
    """Provenance reference for one source article (audit / UI only)."""
    model_config = ConfigDict(extra='ignore')

    article_id: str
    url: str = ''
    title: str = ''
    published_at: Optional[datetime] = None


class RunError(BaseModel):
    """One typed producer-side error (informational; does not block reading)."""
    model_config = ConfigDict(extra='ignore')

    type: str
    message: str = ''
    timestamp: Optional[datetime] = None


class SentimentResult(BaseModel):
    """Per-symbol sentiment outcome inside one envelope."""
    model_config = ConfigDict(extra='ignore')

    symbol: str
    signal: str                       # BUY / SELL / HOLD
    sentiment_score: float = 0.0      # -1.0 .. 1.0
    confidence: float = 0.0           # 0.0 .. 1.0 (0.0 when no news)
    reasoning: str = ''
    urgency: float = 0.0              # 0.0 .. 1.0 (breaking gate input)
    is_breaking: bool = False
    basis: str = ''                   # signal quality: llm / no_data / degraded
    # Newest evidence the row rests on (max fetched_at across its sources). None when the
    # row rests on no evidence at all — which coincides with basis 'no_data'. Per row and
    # not per envelope on purpose: within one envelope some symbols have evidence and
    # others do not, so an envelope-level maximum would report freshness to a row that has
    # none. Lets a decision discount an envelope resting on older evidence than one it
    # already acted on (the producer runs passes concurrently, so a higher seq can carry
    # older evidence).
    evidence_as_of: Optional[datetime] = None
    breaking_episode_id: str = ''          # story identity — distinguishes a new story from a continuing one
    breaking_episode_start: Optional[datetime] = None
    sources: List[ArticleRef] = Field(default_factory=list)

    @field_validator('evidence_as_of', 'breaking_episode_start', mode='before')
    @classmethod
    def _coerce_epoch_ms(cls, value):
        """Normalize epoch-ms (int) → UTC datetime; pass ISO/datetime/None through."""
        return _epoch_ms_to_utc(value)


class AnalysisEnvelope(BaseModel):
    """
    One producer run (all requested symbols) — the engine output, without the
    collector's receive stamp. The archived JSONL line is a SignalSnapshot
    (this plus collected_msc).
    """
    model_config = ConfigDict(extra='ignore')

    schema_version: str
    pipeline_id: str = ''
    outcome_type: str = ''
    # Stream identity (#141 Part 2a). seq is a per-pipeline, gapless counter minted in the
    # producer's insert transaction; stream_epoch changes only when the producer's series
    # was reset. Together they are the ordering primitive AND the dedupe key —
    # (stream_epoch, seq) lexicographic is a total chronological order with no clock in it,
    # because an epoch changes only at boot. seq is unique WITHIN an epoch, not globally.
    # Absent on archive lines predating the stream contract.
    seq: Optional[int] = None
    stream_epoch: Optional[int] = None
    # Why this pass ran: scheduled / boot / breaking / manual / external. Top-level since the
    # producer promoted it out of metadata; older archive lines carry it at metadata.trigger_reason
    # and are normalized on read. It is the ONLY way to tell a scheduled pass from an
    # out-of-band one — timing cannot, because the envelope is stamped at the end of a
    # variable-length run, so scheduled passes land off-grid too.
    trigger_reason: str = ''
    prompt_version: str = ''
    prompt_id: str = ''                    # prompt identity — traceability, must not be lost
    prompt_hash: str = ''                  # prompt content hash — traceability
    data_origin: str = ''                  # 'synthetic' / 'live'; empty = producer predates the field
    config_fingerprint: str = ''           # hash of the producer's effective input config; empty = pre-contract
    timestamp: Optional[datetime] = None   # analysis wall-clock — NOT the merge key
    # When the envelope became fetchable at the producer — the honest availability instant,
    # identical in every copy of the envelope. The no-look-ahead gate resolves against this
    # where it exists; collected_msc (the archiving process's own receive time) is the
    # documented fallback for lines predating the field. Absent NEVER means "equals
    # collected_msc" — it means the pre-field era.
    available_msc: Optional[datetime] = None
    status: str = 'success'                # success / partial / error
    result: List[SentimentResult] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    errors: List[RunError] = Field(default_factory=list)


    @field_validator('available_msc', mode='before')
    @classmethod
    def _coerce_available_msc(cls, value):
        """Normalize epoch-ms (int) → UTC datetime; pass ISO/datetime/None through."""
        return _epoch_ms_to_utc(value)

    @model_validator(mode='before')
    @classmethod
    def _lift_trigger_reason(cls, data):
        """
        Read trigger_reason from metadata when the top-level field is absent.

        The producer promoted it out of metadata; archive lines written before that carry it
        at metadata.trigger_reason. Reading both keeps one reader across the boundary.

        Args:
            data: Raw envelope mapping (before field validation)

        Returns:
            The mapping, with trigger_reason lifted when it was only in metadata
        """
        if not isinstance(data, dict) or data.get('trigger_reason'):
            return data
        legacy = (data.get('metadata') or {}).get('trigger_reason')
        if legacy:
            data = {**data, 'trigger_reason': legacy}
        return data


class SignalSnapshot(AnalysisEnvelope):
    """
    One archived JSONL line: the envelope plus the collector's `collected_msc`
    receive stamp — the no-look-ahead lookup key (resolve nearest collected_msc ≤ tick).

    The wire format of `collected_msc` is epoch milliseconds (UTC, matching the
    tick-side `collected_msc`); it is normalized to a UTC datetime on read so the
    provider compares it against the canonical clock (tick.timestamp). An ISO
    string / datetime is also accepted.
    """
    collected_msc: datetime

    @field_validator('collected_msc', mode='before')
    @classmethod
    def _coerce_collected_msc(cls, value):
        """Normalize epoch-ms (int) → UTC datetime; pass ISO/datetime through."""
        if isinstance(value, (int, float)):
            return datetime.fromtimestamp(value / 1000.0, tz=timezone.utc)
        return value

    def get_resolution_key(self) -> datetime:
        """
        The no-look-ahead gate: the instant this snapshot became usable.

        available_msc (the producer's publish instant, identical in every copy) where it
        exists, collected_msc (the archiving process's receive time) for the pre-field era.

        Returns:
            The instant a decision may first see this snapshot
        """
        return self.available_msc or self.collected_msc

    def get_order_key(self) -> Tuple[int, int, float]:
        """
        Total chronological order key.

        (stream_epoch, seq) lexicographic is chronological with no clock involved, because an
        epoch changes only at a producer boot — everything in epoch N committed before that
        instant, everything in N+1 after it, and seq is chronological within an epoch. Lines
        with no stream identity (the pre-field era) sort by their resolution key ahead of any
        numbered epoch, which is where they belong: the fields only exist from the stream on.

        Returns:
            Sort key; the third element breaks ties for unnumbered lines
        """
        if self.seq is None or self.stream_epoch is None:
            return (-1, -1, self.get_resolution_key().timestamp())
        return (self.stream_epoch, self.seq, 0.0)


class SignalSeries(BaseModel):
    """
    The parsed, time-ordered snapshot collection for one signal source over a
    scenario range. The mountable, picklable payload the data package carries;
    the SignalDataProvider builds its lookup index from it.
    """
    model_config = ConfigDict(extra='ignore')

    signal_kind: str                               # payload kind, e.g. 'llm_sentiment'
    snapshots: List[SignalSnapshot] = Field(default_factory=list)


@dataclass
class ResolvedSignal:
    """
    Provider lookup result for one (timestamp, symbol): the chosen snapshot's
    receive stamp plus the per-symbol sentiment. Returned as None on a gap (no
    snapshot with collected_msc ≤ tick).

    Args:
        collected_msc: Receive stamp of the chosen snapshot
        result: Per-symbol sentiment from that snapshot
    """
    collected_msc: datetime
    result: SentimentResult


class SignalResolution(str, Enum):
    """
    How a SIGNAL worker's result resolved at one tick (#433 Part C).

    Distinct from an ARCHIVE gap: a hole inside the series resolves to the last
    snapshot before it — that is STALE. BLIND means nothing was resolvable at
    all, which in practice only happens at the head of a run.
    """
    FRESH = 'fresh'      # snapshot present, within max_staleness_minutes
    STALE = 'stale'      # snapshot present but aged out
    BLIND = 'blind'      # nothing resolvable at or before the tick


@dataclass
class SignalResolutionStats:
    """
    Per-tick resolution quality of one SIGNAL worker over a run (#433 Part C).

    Counts what the strategy actually DECIDED ON — tick-weighted, not
    refresh-weighted (worker_call_count already measures the snapshot supply).
    The three counters are mutually exclusive and sum to the run's tick count.
    """
    worker_name: str
    signal_kind: str
    symbol: str
    fresh_ticks: int = 0
    stale_ticks: int = 0
    blind_ticks: int = 0
    # Off-tick arrivals (#141 Part 2a): refreshes driven by a live envelope landing
    # BETWEEN two ticks. Deliberately a fourth counter rather than a change of base —
    # the three above are documented to sum to the run's tick count and the ledger's
    # signal_fresh_ratio is defined on that base. Moving to a per-event base is #463's
    # job; doing it early would make this run's ledger rows incomparable with every
    # earlier one.
    off_tick_arrivals: int = 0


# Sentinel `symbol` value for an envelope-level parquet row (#429). One is emitted per
# envelope so every envelope's collected_msc stays resolvable for EVERY covered symbol —
# preserving the v0 behavior where a partial/error snapshot (symbol absent) still resolves
# to a defensive HOLD instead of an earlier snapshot. Not a valid trading symbol.
SIGNAL_ENVELOPE_SYMBOL = '*'


class SignalParquetColumn(str, Enum):
    """
    Columns of the imported signal parquet (#429). Granularity: one row per
    (collected_msc, symbol) for present result symbols, plus one envelope-level row
    (symbol = SIGNAL_ENVELOPE_SYMBOL) per envelope. str-based Enum: values are usable
    directly as DataFrame column names.

    Lean projection: the parquet carries only the worker-consumed fields plus a small
    set of cheap, dictionary-encoded prompt-provenance scalars. The heavy provenance
    (sources / metadata / errors) stays in the raw JSONL archive — the audit source —
    and is deliberately NOT persisted here. SIGNAL_RUNTIME_COLUMNS is what the reader
    projects into the runtime SignalSeries; the prompt-provenance columns are read by
    the index / report path only.
    """
    # --- lookup keys ---
    COLLECTED_MSC = 'collected_msc'      # int64 epoch-ms, the no-look-ahead merge key
    SYMBOL = 'symbol'
    # --- consumed by the worker (from SentimentResult) ---
    SIGNAL = 'signal'
    SENTIMENT_SCORE = 'sentiment_score'
    CONFIDENCE = 'confidence'
    REASONING = 'reasoning'
    URGENCY = 'urgency'
    IS_BREAKING = 'is_breaking'
    BASIS = 'basis'                      # per-symbol signal quality (llm / no_data / degraded)
    STATUS = 'status'                    # envelope status — reconstructs error/empty snapshots
    # --- prompt provenance (traceability — cheap, envelope-scalar) ---
    SCHEMA_VERSION = 'schema_version'
    PIPELINE_ID = 'pipeline_id'
    # --- stream identity + availability (#141 Part 2a; absent in the pre-stream era) ---
    SEQ = 'seq'                          # per-pipeline gapless counter, unique WITHIN an epoch
    STREAM_EPOCH = 'stream_epoch'        # bumped only when the producer's series was reset
    AVAILABLE_MSC = 'available_msc'      # int64 epoch-ms, the producer's publish instant
    EVIDENCE_AS_OF = 'evidence_as_of'    # int64 epoch-ms per row; null = the row rests on no evidence
    PROMPT_VERSION = 'prompt_version'
    PROMPT_ID = 'prompt_id'
    PROMPT_HASH = 'prompt_hash'
    DATA_ORIGIN = 'data_origin'          # 'synthetic' (generated) / 'live' / '' (pre-contract)
    CONFIG_FINGERPRINT = 'config_fingerprint'   # producer input-config hash / '' (pre-contract)
    # Why the producing pass ran: scheduled / boot / breaking / manual / external / ''.
    # The ONE field lifted out of the envelope's `metadata` (which is otherwise archive-only,
    # see SIGNAL_RUNTIME_COLUMNS' note) — it is a short scalar of the same weight class as the
    # provenance columns above, and it replaces a timing heuristic with a stated fact.
    TRIGGER_REASON = 'trigger_reason'


# What the reader loads into the runtime SignalSeries (projection — ship only consumed
# fields, the seam shared with #128). collected_msc + symbol are the lookup keys; status
# reconstructs error/empty (defensive-HOLD) snapshots; basis carries per-symbol signal
# quality; schema_version is required to build the SignalSnapshot model. The prompt-
# provenance scalars (pipeline_id / prompt_version / prompt_id / prompt_hash) are NOT
# loaded at runtime.
SIGNAL_RUNTIME_COLUMNS = frozenset({
    SignalParquetColumn.COLLECTED_MSC.value,
    SignalParquetColumn.SYMBOL.value,
    SignalParquetColumn.SIGNAL.value,
    SignalParquetColumn.SENTIMENT_SCORE.value,
    SignalParquetColumn.CONFIDENCE.value,
    SignalParquetColumn.REASONING.value,
    SignalParquetColumn.URGENCY.value,
    SignalParquetColumn.IS_BREAKING.value,
    SignalParquetColumn.BASIS.value,
    SignalParquetColumn.STATUS.value,
    SignalParquetColumn.SCHEMA_VERSION.value,
    # Stream identity is runtime, not provenance: it orders the series and deduplicates a
    # redelivered envelope, and the availability stamp is the resolution gate itself.
    SignalParquetColumn.SEQ.value,
    SignalParquetColumn.STREAM_EPOCH.value,
    SignalParquetColumn.AVAILABLE_MSC.value,
    SignalParquetColumn.EVIDENCE_AS_OF.value,
})
