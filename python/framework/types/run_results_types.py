"""
Run-results ledger types.

Runtime domain types for the persistent run-results ledger (the substrate of the
Parameter Optimization system). `RunProvenance` is the per-run provenance bundle
written alongside the run's KPIs; `SweepContext` is the optional sweep tagging a
combination carries into a batch so the ledger row can be grouped by sweep;
`BookingSegment` is one closed booking period of a live deployment (#537).
"""

from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from python.framework.types.api.report_types import RunSummaryCurrency


@dataclass
class SweepContext:
    """Sweep tagging threaded into a single combination's batch run."""
    sweep_id: str
    sweep_params: Dict[str, Any]    # the combination's concrete grid point {path: value}
    objective: str = 'expectancy'   # the spec's ranking objective (recorded so report defaults to it)
    maximize: bool = True           # the spec's ranking direction
    # How many combinations the SEARCH spans (#32). A property of the search rather than of any
    # run in it, which is why it has to travel with the tagging: no run can count its siblings.
    trial_count: int = 1


@dataclass
class RunProvenance:
    """Per-run provenance written to the ledger next to the run's KPIs."""
    param_hash: str                 # fingerprint of the effective strategy_config (leading key)
    status: str                     # 'ok' | 'error' (from the canonical WarningsErrorsOutcome)
    error: Optional[str]            # failure reason when status == 'error', else None
    run_id: str                     # run-timestamp dir name (join key → full run io/)
    run_timestamp: datetime         # UTC
    scenario_set_name: str
    # The program version that produced the run. Git identity says WHICH CODE; this says
    # which RELEASE it was, and a run artifact could not answer that at all before — the
    # field existed in app_config and was read by the HTTP API alone.
    app_version: str
    git_commit: Optional[str]
    git_branch: Optional[str]
    git_dirty: bool
    decision_logic_type: str
    decision_version: str
    worker_versions: Dict[str, str]     # worker instance name → ComponentMetadata.version
    config_snapshot: str                # full resolved strategy_config (JSON string)
    symbols: List[str]
    data_broker_type: str
    sweep_id: Optional[str] = None      # null for non-sweep runs
    sweep_params: Optional[Dict[str, Any]] = None
    sweep_objective: Optional[str] = None    # the sweep spec's objective (report defaults to it)
    sweep_maximize: Optional[bool] = None    # the sweep spec's rank direction
    # ---- What this run CONSUMED (#518) -------------------------------------------------
    # Recorded HERE and deliberately not in the run header. The header is written at the run's
    # START, before anything is mounted, and has a write and a read and no update path by
    # design — at that moment nothing is known about what will be read. This record is built
    # from a FINISHED run, so it is the first artifact that can answer the question at all.
    #
    # The argument is `logic_version`'s, one drawer over: a ranking that cannot tell which DATA
    # a row was produced over is comparing runs that read different things under one column
    # name. For the thirty-day parity proof that is not a nicety — a live run and the backtest
    # it is compared against have to be shown to have read the same archive.
    #
    # They sit apart from `data_broker_type`, which they belong beside, only because a dataclass
    # puts every defaulted field after every undefaulted one.
    #
    # `input_plane` is what keeps an empty value honest: a LIVE session consumes a socket and
    # has no archive input, so its three joined strings are empty BY CONSTRUCTION. Without this
    # field that emptiness would be indistinguishable from a sim run whose recording broke —
    # the same bytes for "nothing to read" and "we were not looking".
    input_plane: str = ''                   # 'archive' (sim) | 'stream' (live)
    data_format_versions: str = ''          # distinct, sorted, comma-joined
    origin_classes: str = ''                # distinct, sorted, comma-joined
    origin_evidence_grades: str = ''        # distinct, sorted, comma-joined
    input_files: int = 0                    # files the scenarios actually read
    unstamped_input_files: int = 0          # of those, the ones not production-and-stamped
    # WHICH PRICE the bars this run read were rendered from (§31c). Joined-distinct like the
    # three above, because a run spans brokers and timeframes and a half-re-rendered archive
    # legitimately answers 'order_driven,unknown' — which is the condition the stamp exists to
    # expose rather than to smooth over. On the LIVE side it is a DECLARATION and not a stamp;
    # `input_plane` is what tells the two apart.
    price_bases: str = ''                   # distinct, sorted, comma-joined
    # WAS this run declared part of a continuous deployment, and WHICH one (#497). The
    # RESOLVED answer: a profile declaring continuous under `--one-off` records ''. Without
    # it a reader cannot tell a session that belongs to no deployment from one whose
    # grouping was simply never written — the same distinction `input_plane` draws for the
    # empty consumption fields.
    deployment_id: str = ''
    # Fingerprint of the OPERATIONAL half of the profile — everything except strategy_config,
    # which `param_hash` already covers. Two hashes because they answer two questions: whether
    # the STRATEGY moved (what #512 compares on) and whether the OPERATION moved (a risk
    # threshold, a timeout, a guard). One value answering both would answer neither — a
    # changed stop level must not read as a different strategy.
    profile_hash: str = ''
    # WHICH PIPELINE produced this run — 'simulation' | 'live', taken from the same constants
    # the run tree is laid out with (`log_layout_types.RUN_TYPE_*`) rather than a literal, so
    # the ledger, the run index and the directory on disk cannot drift into three vocabularies.
    run_type: str = ''
    # How many candidates this run was selected FROM (#32). One for a run nobody swept, which is
    # a statement and not a placeholder: an absent value means the row predates the field, while
    # 1 means one attempt, and the two must not read alike.
    #
    # It is recorded because it CANNOT be recovered afterwards. Every other input to the Deflated
    # Sharpe Ratio (Bailey & López de Prado) — which discounts a result by how many attempts
    # produced it — can be re-derived from the stored runs; this one describes the SEARCH, and a
    # search leaves no other trace once it is over. A strategy picked as the best of 500 grid
    # points is not the evidence a strategy picked out of 5 is, and without this field the two
    # are indistinguishable.
    #
    # It is the size of the search as PLANNED. How many of those combinations actually reached
    # the ledger is countable from the rows carrying this `sweep_id`, so the pair says whether
    # the search completed — the same self-checking shape as a control total (§48).
    trial_count: int = 1


class SegmentCloseReason(Enum):
    """
    Why a booking segment was closed. Recorded, never inferred.

    Without it a hand-triggered close is indistinguishable from a shifted day boundary once
    the run is over, and SESSION_END is load-bearing beyond readability: it is what tells a
    reader that a deployment's books are complete. The two-store completion audit (§44) used
    to derive that from the ledger row existing at all — which stops working the moment a
    session books DURING its run (#537).
    """
    ANCHOR = 'anchor'               # the market's own trading-day boundary (§47)
    SESSION_END = 'session_end'     # the session ended; the open segment is closed rather than dropped
    OPERATOR = 'operator'           # a person asked for a close


@dataclass
class BookingSegment:
    """
    One closed booking period of one run unit — the HAUPTBUCH entry of this system.

    The model is ordinary double-entry bookkeeping, and naming it that way is not decoration:
    the trade records are the GRUNDBUCH (the journal of individual bookings, chronological),
    a segment is the HAUPTBUCH entry (the period summary per account), and everything above
    it — a deployment's total, a Sharpe ratio, a drawdown over a month — is the ABSCHLUSS
    derived from those periods. The reason the construction is trustworthy is the same reason
    it has been for centuries: the summary is believed because it can be RECOMPUTED from the
    entries behind it, and it carries a control total that lets it disprove itself (§48).

    Keyed by a RUNNING NUMBER inside its unit rather than by a date, deliberately: a date
    cannot express two closes on one day, and the industry books more than once a day in
    several places — perpetual funding every eight hours, an intraday margin call, an
    operator's period close. A date would force `2026-09-21_2` on the next person.

    Args:
        segment_no: Running number within the UNIT, starting at 1. For a live session it
            survives a restart through the cold-start carry-over, the same way the position
            counter does (#355); a simulation scenario starts at 1 every time, because a
            backtest has no history to inherit
        unit_name: Which run unit this period belongs to — a scenario in the simulation, the
            session in live. Load-bearing rather than decorative: a run's scenarios cover
            DIFFERENT windows (measured 2026-09-21: 40 scenarios, 40 distinct windows), so
            "day 1 of the run" is not a thing and only "day 1 of this unit" is
        opened_at: When the period began, from the CANONICAL clock — the seal is an event (§9)
        closed_at: When it ended, same clock. Read from the record and never re-derived from
            config: during an anchor change the config describes what a run WOULD produce
            while existing rows still hold the previous answer (§31c makes the same argument
            for the bar basis)
        reason: What closed it
        trade_count: How many trade records the period's figures were derived from. This is a
            CONTROL TOTAL (§48), not a statistic: it is what lets a reader re-derive the row
            from the records and find out that it does not match
        figures: The period's KPIs for ONE account currency — the same shape a whole run
            reports, so a period and a run describe themselves with one vocabulary. A unit
            trading two account currencies emits one segment per currency
        segment_max_equity: The highest account value seen INSIDE this period
        segment_min_equity: The lowest. Not derivable from the two above it — the peak and the
            trough belong to different moments, so `high - drawdown` is a different number
            from the low whenever the peak came after it
        segment_max_drawdown: The deepest decline inside this period, against ITS OWN running
            peak. Beside it, `figures.account_max_drawdown` carries the CUMULATIVE decline of
            the whole deployment, and both are needed: the cumulative one keeps `max()` correct
            over rows, the own one answers how far this single day fell
    """
    segment_no: int
    unit_name: str
    opened_at: datetime
    closed_at: datetime
    reason: SegmentCloseReason
    trade_count: int
    figures: RunSummaryCurrency
    segment_max_equity: float = 0.0
    segment_min_equity: float = 0.0
    segment_max_drawdown: float = 0.0


class Reduction(Enum):
    """
    How a ledger column combines when several rows are read as one.

    The question a reader actually has is not "is this aggregated" but "how do I combine it",
    and the two do not line up: `net_pnl` and `win_rate` are both aggregates, one sums and the
    other cannot be combined at all. Marking both as aggregates would make them look alike,
    which is precisely the confusion that produces a wrong total (CLAUDE.md §48).

    Declared per column in `run_results_ledger.COLUMN_REDUCTION`, and a test holds that map and
    LEDGER_COLUMNS to the same key set — so a new column cannot be added without saying how it
    reduces.
    """
    SUM = 'sum'             # a flow or a counter: rows partition it, adding them is correct
    MAX = 'max'             # a cumulative extremum. NEVER sum — one decline counted once per row
    DERIVE = 'derive'       # a rate, a mean, a quotient: NOT combinable, re-derive from records
    LAST = 'last'           # a stock read at an instant; the most recent row wins
    IDENTITY = 'identity'   # must agree across the rows, or they were never comparable
    UNION = 'union'         # a comma-joined set: combine by union, never by concatenation
    MIN = 'min'             # a cumulative minimum — the trough, the mirror of MAX
    # An instant that is one END of a range. `SPAN` alone was not enough and the gap only showed
    # when something finally READ the map: over several rows the earliest and the latest both
    # mean something, while an aggregated row has ONE slot — so the declaration has to say which
    # end belongs in it, or every caller decides for itself and they disagree.
    SPAN_START = 'span_start'   # the earliest — a beginning, an opening
    SPAN_END = 'span_end'       # the latest — an end, a close, a stamp
    # Reduced WITH another column, never on its own. The drawdown trio is the case: whichever
    # row owns the deepest decline also supplies the peak it fell from and the share it was,
    # and taking each by its own max pairs one row's trough with another's peak — exactly the
    # defect #497 removed from the console aggregate (report_aggregators.py:160-163 states it).
    # The companion's comment names the column that LEADS it.
    COMPANION = 'companion'
