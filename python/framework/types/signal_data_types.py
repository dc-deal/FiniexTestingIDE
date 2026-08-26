"""
FiniexTestingIDE - Signal Data Types
Typed schema for pre-collected external signal data (SIGNAL worker input, #141).

The producer emits one AnalysisEnvelope per run (all symbols); the collector archives
each as one JSONL line plus a `collected_msc` receive stamp (the no-look-ahead merge key).
Pydantic models validate the external schema on read; `extra='ignore'` keeps the reader
tolerant of producer-side metadata additions (only schema_version + the consumed result
fields are the strict contract).
"""

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Dict, List, Optional, Set, Tuple

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


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
    # Story identity (producer #65, live 2026-08-24). Set on every pass the producer counts as
    # INSIDE the episode — the opener, a hold-band pass (is_breaking false, urgency at or above
    # the exit threshold) and a dip inside the gap — so it does NOT track is_breaking. Use the id
    # for episode identity, is_breaking for 'this pass crossed the threshold'.
    # OPAQUE by the producer's contract: it reads '<pipeline_id>:<query>:<start>' and is meant to
    # be legible in a log line, but the middle segment is free-text pipeline config and the string
    # carries further ':' plus spaces and '/'. Never split it, never derive the symbol or the start
    # instant from it — what it guarantees is byte equality: same story, same value. Length is
    # bounded by their query text (70-100 chars today), so store it as variable-length text and
    # encode it if it ever reaches a URL path or a filename.
    # Arrives as JSON null outside an episode and is normalized to the empty string here:
    # every consumer then asks one question ('is there an id') instead of two, and the parquet
    # column stays a plain string. Also empty on everything archived before the field existed.
    breaking_episode_id: str = ''
    # A FLAG, not a timestamp: true only on the pass that opened the episode.
    breaking_episode_start: bool = False
    sources: List[ArticleRef] = Field(default_factory=list)

    @field_validator('evidence_as_of', mode='before')
    @classmethod
    def _coerce_epoch_ms(cls, value):
        """Normalize epoch-ms (int) → UTC datetime; pass ISO/datetime/None through."""
        return _epoch_ms_to_utc(value)

    @field_validator('breaking_episode_id', mode='before')
    @classmethod
    def _coerce_absent_episode(cls, value):
        """Normalize a null episode id to '' — 'outside an episode' is one state, not two."""
        return '' if value is None else value


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
    # Set by the importer so a PROJECTED series keeps the envelope-level value; absent on
    # the wire, where the envelope is complete and the row maximum IS the envelope's.
    envelope_evidence_as_of: Optional[datetime] = None

    @field_validator('collected_msc', 'envelope_evidence_as_of', mode='before')
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

    def get_evidence_as_of(self) -> Optional[datetime]:
        """
        Newest evidence this ENVELOPE rests on — the max across its rows.

        The unit matters and is the whole point of the accessor. A ROW's stamp may
        legitimately fall between two passes, because its retrieved set changes (a young
        article slides out of the recency window, the similarity floor cuts differently).
        Comparing rows therefore reports a "regression" constantly. The producer's passes
        are what can overtake each other, so the comparison belongs on the envelope.

        Returns:
            Newest evidence stamp across all rows, or None when no row rests on evidence
        """
        if self.envelope_evidence_as_of is not None:
            return self.envelope_evidence_as_of
        stamps = [row.evidence_as_of for row in self.result
                  if row.evidence_as_of is not None]
        return max(stamps) if stamps else None

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
        evidence_as_of: Newest evidence the whole ENVELOPE rests on (None when it rests
            on none). Envelope-level on purpose — see SignalSnapshot.get_evidence_as_of.
    """
    collected_msc: datetime
    result: SentimentResult
    evidence_as_of: Optional[datetime] = None


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


class SignalSeriesKind(str, Enum):
    """
    Where a signal series came from — which decides what can be said about it.

    An ARCHIVE was collected, finished and can be analysed for continuity against a market
    calendar. A FEED is being received right now: it has no "could have offered" plane at
    all, because there is no window it either covered or missed.

    The distinction is a field rather than an inference because the alternative is a
    default that asserts: an absent gap analysis renders as "no gaps", which reads as
    "analysed and seamless" for something that was never analysable.
    """
    ARCHIVE = 'archive'
    FEED = 'feed'


class SignalSourceMode(str, Enum):
    """
    What feeds a session's SIGNAL workers — resolved once, then followed everywhere.

    Three states, and the order they are asked in matters: a profile without a SIGNAL
    worker needs no source at all, so the installation-wide transport settings do not
    apply to it. Only a session that HAS such a worker and NO mounted series needs a
    live transport.

    The enum exists because this question used to be answered separately at each site
    that cared, and the answers disagreed: one place aborted a session that needed no
    source, another opened a live connection into a session that already had a mounted one.
    """
    NONE = 'none'
    MOUNTED = 'mounted'
    LIVE = 'live'


class SignalTransportKind(str, Enum):
    """
    Which live transport carries the envelopes.

    LIVE is deliberately not a synonym for polling: the push stream is a second transport
    over the same inbox, so the mode says THAT a transport is needed and this says WHICH.
    """
    POLL = 'poll'
    STREAM = 'stream'


@dataclass
class SignalSourceResolution:
    """
    The resolved answer, carried instead of re-derived.

    `signal_kind` and `transport` are set for LIVE only — the mounted case resolves per
    worker against the package, and the NONE case has nothing to resolve. `reason` is the
    one line written to the session log, so an operator reading the log sees which branch
    was taken and why.
    """
    mode: SignalSourceMode
    worker_count: int
    reason: str
    signal_kind: Optional[str] = None
    transport: Optional[SignalTransportKind] = None


@dataclass
class SignalObservedSeries:
    """
    What a series of envelopes states about itself — independent of where it came from.

    The half of the signal picture that a live feed and a stored archive have in common:
    provenance, composition, cadence, extent and stream position. Produced two ways —
    read from parquet by SignalCoverageReport, or accumulated from arrivals by the live
    transport — so one report shape serves both pipelines.

    Deliberately does NOT carry gaps or window coverage. Those classify continuity against
    a market calendar and presuppose a finished archive; a live feed's outage plane is the
    disturbance-episode protocol instead, which derives its spans from observed state.

    Args:
        source: Signal source identity (the archive's / producer's pipeline_id)
        symbol: Trading symbol the series is scoped to, '' for an envelope-level view
        kind: Whether these facts were read from an archive or received from a feed
        snapshot_count: Envelopes the archive holds / the session received
        start_time: First envelope in the series
        end_time: Last envelope in the series
        cadence_seconds: Distance between envelopes — measured for an archive, the
            producer's own reported interval for a feed (three arrivals is not a sample)
        data_origins: Distinct data_origin values seen; empty = the producer predates it
        config_fingerprints: Distinct producer input-config hashes seen
        prompt_versions: Distinct prompt generations seen. A bump marks a SERIES BREAK —
            different prompts yield different scores for the same news — so a run holding
            more than one value spans two series and must say so. Filled on the feed side,
            where the envelope carries it; empty for an archive, whose runtime projection
            deliberately does not load the prompt provenance
        trigger_reasons: Envelope counts per trigger_reason
        trigger_unknown: Envelopes carrying no reason — kept apart so a partially stamped
            series does not render as if the composition covered everything
        envelopes_with_stream_identity: How many carry seq/epoch at all
        seq_span: (first, last) seq observed, None when the era carries none
        seq_holes: Missing positions within an epoch
        stream_epochs: Distinct epochs seen; more than one means the series spans a
            producer restart
    """
    source: str
    symbol: str = ''
    kind: SignalSeriesKind = SignalSeriesKind.ARCHIVE
    snapshot_count: int = 0
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    cadence_seconds: float = 0.0
    data_origins: Set[str] = field(default_factory=set)
    config_fingerprints: Set[str] = field(default_factory=set)
    prompt_versions: Set[str] = field(default_factory=set)
    trigger_reasons: Dict[str, int] = field(default_factory=dict)
    trigger_unknown: int = 0
    envelopes_with_stream_identity: int = 0
    seq_span: Optional[Tuple[int, int]] = None
    seq_holes: int = 0
    stream_epochs: Set[int] = field(default_factory=set)

    def get_sequence_description(self) -> str:
        """
        One line describing the stream position, or why there is none.

        Returns:
            'contiguous 82→84' / '2 holes 82→90' / 'not verifiable (no seq in this era)'
        """
        if self.seq_span is None:
            return 'not verifiable (no seq in this era)'
        first, last = self.seq_span
        span = f'{first}→{last}'
        return f'{self.seq_holes} holes {span}' if self.seq_holes else f'contiguous {span}'


# Major schema versions our reader understands, shared by the archive path and the live
# transport — two copies would let the two disagree about what we can read, and the whole
# point of the gate is that both answer the same way.
#
# '1' — the original contract.
# '2' — the stream contract (#141 Part 2a): adds seq / stream_epoch / available_msc /
#       evidence_as_of, and RELOCATES trigger_reason out of metadata to the top level.
#       That relocation is why the producer spent a major on an otherwise additive group:
#       a minor would not have fired this gate, and the fallback that reads the old
#       location lives behind it. Both majors are read by one model — see
#       AnalysisEnvelope._lift_trigger_reason.
#
# From their #65 note onward the producer bumps the MINOR for an additive field and the
# MAJOR for a breaking one, so pinning the major is the supported way to stay readable
# while the shape grows.
SUPPORTED_SCHEMA_MAJORS = frozenset({'1', '2'})


def schema_major(version: str) -> str:
    """
    Major component of an 'X.Y' schema version string.

    Args:
        version: The declared schema version

    Returns:
        The major component, or the whole string when it carries no separator
    """
    return version.split('.', 1)[0]


class SignalEdge(str, Enum):
    """
    Transition of a boolean signal property between two consecutively served envelopes.

    Derived on our side in BOTH pipelines rather than consumed from the producer's own
    filtered view (#141 Part 2a). If the producer derived it live while we derived it in
    simulation, the two derivations could drift and the disagreement would be invisible —
    each side internally consistent, the pair silently wrong. Same rule as the disturbance
    episodes (#451): a boundary is always derived from observed state; an upstream
    declaration may contribute a label, never a boundary.
    """
    ENTERED = 'entered'
    EXITED = 'exited'
    NONE = 'none'


class SignalEpisodeEdge(str, Enum):
    """
    Transition of the producer's breaking-EPISODE identity between two served envelopes.

    Separate from SignalEdge because an episode is not a boolean, and because it carries the
    one case a boolean cannot express: one story ending and another beginning with no quiet
    pass between them. Against `is_breaking` that reads as nothing at all — the flag simply
    stays true — which is why the producer's own guidance is to gate on the identity.

    The restraint is the same as SignalEdge's, for the same three reasons: no previous
    observation, a gap, and an overtaking pass all report NONE. The identity is the
    producer's label; the boundary is still derived here.
    """
    OPENED = 'opened'     # no episode -> inside one
    CHANGED = 'changed'   # inside one episode -> inside a DIFFERENT one, no gap between
    CLOSED = 'closed'     # inside an episode -> outside
    NONE = 'none'         # the same episode, or nothing a transition could be read from


@dataclass
class SignalHealthStatus:
    """
    Identity of the producer engine a live session is consuming from (#141 Part 2a).

    Exists because nothing on an envelope says which store it came from. Two producer
    instances can share a schema, a pipeline_id and a seq range, so a measurement taken
    against a development instance is indistinguishable from one taken against the
    series a release is certified on — unless the journal is asked and recorded.

    The id binds and the name does not: the id is a fingerprint of the producer's
    database cluster, fixed at its creation, while the name is looked up from a
    per-machine mapping on the producer side and may be renamed at any time. Certify
    against the id, read the name.

    Args:
        journal_id: Cluster fingerprint, None when the producer has no store attached
            or cannot read its own identifier — either way the session is not certifiable
        journal_name: The producer's label for that journal, 'unknown' when its lookup
            missed, empty before the first answer
        engine_version: Producer version string
        pass_timeout_s: How long a producer pass may run — bounds how late an envelope
            can legitimately be
        probed_at: When the last answer arrived (wall clock: this measures our
            observation, not market time)
        journal_changed: Set once the identity changed mid-session. Sticky, because the
            cursor built against the previous journal is meaningless in the new one
        probe_errors: Times the probe could not reach the producer
        producer_cadence_s: How often the producer evaluates OUR source, as it reports it.
            The authoritative value for what we otherwise only configure
        budget_suspended: The producer has stopped evaluating because a spending limit
            was reached. Downstream this looks exactly like a silent producer, so the
            reason is worth carrying rather than rediscovering from the silence
        budget_reason: What the producer says about the suspension
    """
    journal_id: Optional[str] = None
    journal_name: str = ''
    engine_version: str = ''
    pass_timeout_s: Optional[float] = None
    probed_at: Optional[datetime] = None
    journal_changed: bool = False
    probe_errors: int = 0
    producer_cadence_s: Optional[float] = None
    budget_suspended: bool = False
    budget_reason: Optional[str] = None

    def is_identified(self) -> bool:
        """
        Whether the producer named a journal.

        Returns:
            True when an identity is known
        """
        return bool(self.journal_id)


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
    # Episode identity (producer #65). Runtime, not provenance: gating on the episode instead
    # of the raw is_breaking edge is the whole point of the field, and a decision that cannot
    # read it in simulation cannot be backtested. Absent on parquet written before this column
    # existed — the reader defaults, which IS the pre-field era's meaning.
    BREAKING_EPISODE_ID = 'breaking_episode_id'
    BREAKING_EPISODE_START = 'breaking_episode_start'   # flag: this pass opened the episode
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
    # Envelope-level evidence, repeated on every row of the envelope like status and
    # schema_version. It exists because the runtime series is PROJECTED to one symbol: a
    # projected snapshot holds one row, so a max over its rows is the row's own stamp, not
    # the envelope's. Without this column the RC-4 comparison would mean something
    # different in simulation than on the wire (measured: 237 vs 17 on one mock week).
    ENVELOPE_EVIDENCE_AS_OF = 'envelope_evidence_as_of'
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
    SignalParquetColumn.BREAKING_EPISODE_ID.value,
    SignalParquetColumn.BREAKING_EPISODE_START.value,
    SignalParquetColumn.BASIS.value,
    SignalParquetColumn.STATUS.value,
    SignalParquetColumn.SCHEMA_VERSION.value,
    # Stream identity is runtime, not provenance: it orders the series and deduplicates a
    # redelivered envelope, and the availability stamp is the resolution gate itself.
    SignalParquetColumn.SEQ.value,
    SignalParquetColumn.STREAM_EPOCH.value,
    SignalParquetColumn.AVAILABLE_MSC.value,
    SignalParquetColumn.EVIDENCE_AS_OF.value,
    SignalParquetColumn.ENVELOPE_EVIDENCE_AS_OF.value,
})


@dataclass
class ProducerRead:
    """
    Outcome of one JSON read against a producer route.

    A result rather than an exception because both callers need the DISTINCTION, not the
    stack: the producer's connect contract states that 401 is not a transport failure, so
    "the token was refused" and "nothing answered" must stay separable all the way to the
    operator.

    Args:
        ok: Whether the route answered with a decodable payload
        payload: The decoded response, None on failure
        detail: One line describing what came back, or why nothing did
        credential_rejected: The producer refused the credential (401 / 403)
        status_code: HTTP status when the producer answered with one
    """
    ok: bool
    payload: Optional[Dict[str, Any]] = None
    detail: str = ''
    credential_rejected: bool = False
    status_code: Optional[int] = None


@dataclass
class ConnectCheckStep:
    """
    Outcome of one probed route.

    Args:
        name: Route as the operator recognizes it
        ok: Whether the route answered as expected
        detail: One line describing what came back, or why nothing did
        payload: Decoded response when there was one
    """
    name: str
    ok: bool
    detail: str
    payload: Optional[Dict[str, Any]] = None


@dataclass
class ConnectCheckResult:
    """
    Everything one connect check established.

    Args:
        endpoint_name: Registered endpoint the address came from ('dev', 'production', …)
        base_url: Address probed, as configured
        credential_source: File the token came from — never the token itself (§29)
        credential_configured: Whether a non-empty token was sent
        steps: Per-route outcomes in probe order
        credential_rejected: True when the producer refused the token
    """
    endpoint_name: str
    base_url: str
    credential_source: str
    credential_configured: bool
    steps: List[ConnectCheckStep] = field(default_factory=list)
    credential_rejected: bool = False

    def is_ok(self) -> bool:
        """
        Whether every probed route answered as expected.

        Returns:
            True when no step failed
        """
        return all(step.ok for step in self.steps)
