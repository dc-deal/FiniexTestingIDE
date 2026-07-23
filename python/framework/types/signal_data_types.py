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
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


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
    sources: List[ArticleRef] = Field(default_factory=list)


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
    prompt_version: str = ''
    prompt_id: str = ''                    # prompt identity — traceability, must not be lost
    prompt_hash: str = ''                  # prompt content hash — traceability
    timestamp: Optional[datetime] = None   # analysis wall-clock — NOT the merge key
    status: str = 'success'                # success / partial / error
    result: List[SentimentResult] = Field(default_factory=list)
    metadata: Dict[str, Any] = Field(default_factory=dict)
    errors: List[RunError] = Field(default_factory=list)


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


class SignalSeries(BaseModel):
    """
    The parsed, time-ordered snapshot collection for one signal source over a
    scenario range. The mountable, picklable payload the data package carries;
    the SignalDataProvider builds its lookup index from it.
    """
    model_config = ConfigDict(extra='ignore')

    source: str                                    # e.g. 'llm_sentiment'
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
    PROMPT_VERSION = 'prompt_version'
    PROMPT_ID = 'prompt_id'
    PROMPT_HASH = 'prompt_hash'


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
})
