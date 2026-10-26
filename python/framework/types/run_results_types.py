"""
Run-results ledger types.

Runtime domain types for the persistent run-results ledger (the substrate of the
Parameter Optimization system). `RunProvenance` is the per-run provenance bundle
written alongside the run's KPIs; `SweepContext` is the optional sweep tagging a
combination carries into a batch so the ledger row can be grouped by sweep;
`BookingPeriod` is one closed booking period of a live deployment (#537).
"""

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from python.framework.types.api.report_types import RunSummaryCurrency
from python.framework.types.run_kind_types import OrdersTo
from python.framework.types.run_outcome_types import RunOutcome


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
    run_id: str                     # the run's id (join key → the run's io/)
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
    strategy_config_json: str           # full resolved strategy_config (JSON string)
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
    # name. For the thirty-day parity proof that is not a nicety — the backtest a real-money
    # session is compared against has to be shown to have read production-class, stamped data
    # our own collector recorded in the session's window. The session itself read a socket, as
    # `input_plane` below says, so its own row cannot show it.
    #
    # They sit apart from `data_broker_type`, which they belong beside, only because a dataclass
    # puts every defaulted field after every undefaulted one.
    #
    # `input_plane` is what keeps an empty value honest: a LIVE-ADAPTER session consumes a socket and
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
    # WHICH BOT this was, as the profile DECLARED it (#538) — live only, empty in the
    # simulation and on a profile that declares none. It is the one identity here that does not
    # move: `run_name` is what the profile is CALLED and an operator improves that, while
    # `deployment_id` is minted per deployment and a `--new-deployment` starts a fresh one. So a
    # reader asking "is this the same bot as the row above" has no other column to ask. It is
    # also what the bot's carry-over state is filed under, which is what makes the ledger row and
    # the position book joinable at all.
    bot_id: str = ''
    # Fingerprint of the OPERATIONAL half of the profile — everything except strategy_config,
    # which `param_hash` already covers. Two hashes because they answer two questions: whether
    # the STRATEGY moved (what #512 compares on) and whether the OPERATION moved (a risk
    # threshold, a timeout, a guard). One value answering both would answer neither — a
    # changed stop level must not read as a different strategy.
    profile_hash: str = ''
    # WHICH PIPELINE produced this run — 'simulation' | 'autotrader', taken from the same constants
    # the run tree is laid out with (`run_layout_constants.RUN_TYPE_*`) rather than a literal, so
    # the ledger, the run index and the directory on disk cannot drift into three vocabularies.
    run_type: str = ''
    # WHERE this run's orders went — a simulator or the venue, i.e. whether real money moved
    # (#362). The value the run header records, by the same rule: a backtest is SIMULATED by
    # construction, a session resolves its dry-run posture. A deployment's rows are only
    # comparable when they agree on it, and the money figures of a deployment that mixes the
    # two add simulated P&L to real. None where the rule refused the profile.
    orders_to: Optional[OrdersTo] = None
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
    # How the run ENDED and what its channels held — taken from the warnings-errors outcome, which
    # counts them once for both pipelines. None when no report was handed over: not counted,
    # never zero.
    run_outcome: Optional[RunOutcome] = None
    error_count: Optional[int] = None       # ERROR records in the error pot
    warning_count: Optional[int] = None     # Tier-1 findings (validator-produced)
    log_warning_count: Optional[int] = None # Tier-2 WARNING records in the log pot
    # WHAT the run ran with (#547), carried onto every row (#476): the run-config store id of its
    # rendered configuration as its header names it, and of the broker configuration it froze —
    # comma-joined where a backtest traded under several. A session books its trading days
    # while it runs, so a session killed on day 29 names both in the rows it left rather than in
    # a log line. '' where the run froze none.
    rendered_config_id: str = ''
    broker_config_id: str = ''


class PeriodCloseReason(Enum):
    """
    Why a booking period was closed. Recorded, never inferred.

    Without it a hand-triggered close is indistinguishable from a shifted day boundary once
    the run is over, and SESSION_END is load-bearing beyond readability: it is what tells a
    reader that a deployment's books are complete. The two-store completion audit (§44) used
    to derive that from the ledger row existing at all — which stops working the moment a
    session books DURING its run (#537).
    """
    ANCHOR = 'anchor'               # the market's own trading-day boundary (§47)
    SESSION_END = 'session_end'     # the session ended; the open period is closed rather than dropped
    OPERATOR = 'operator'           # a person asked for a close


@dataclass
class BookingPeriod:
    """
    One closed booking period of one run unit — the LEDGER entry of this system.

    The model is ordinary double-entry bookkeeping, and naming it that way is not decoration:
    the trade records are the JOURNAL (individual bookings, chronological), a period is the
    LEDGER entry (the period summary per account), and everything above it — a deployment's
    total, a Sharpe ratio, a drawdown over a month — is the CLOSING derived from those
    periods. The reason the construction is trustworthy is the same reason
    it has been for centuries: the summary is believed because it can be RECOMPUTED from the
    entries behind it, and it carries a control total that lets it disprove itself (§48).

    Keyed by a RUNNING NUMBER inside its unit rather than by a date, deliberately: a date
    cannot express two closes on one day, and the industry books more than once a day in
    several places — perpetual funding every eight hours, an intraday margin call, an
    operator's period close. A date would force `2026-09-21_2` on the next person.

    Args:
        period_no: Running number within the UNIT, starting at 1. For an AutoTrader session it
            survives a restart through the cold-start carry-over, the same way the position
            counter does (#355); a simulation scenario starts at 1 every time, because a
            backtest has no history to inherit
        unit_name: Which run unit this period belongs to — a scenario in the simulation, the
            session in live. Load-bearing rather than decorative: a run's scenarios cover
            DIFFERENT windows (measured 2026-09-21: 40 scenarios, 40 distinct windows), so
            "day 1 of the run" is not a thing and only "day 1 of this unit" is
        opened_at: When the period began, from the CANONICAL clock — the seal is an event (§9).
            The previous period's close, so the periods leave no stretch of the run uncovered:
            after a market's closed days it is the instant the last trading day ended, not the
            instant the next one opened
        closed_at: When it ended, same clock — the trading day's boundary instant for a day the
            market ended (#476), not the moment the run noticed it. Read from the record and
            never re-derived from config: during an anchor change the config describes what a
            run WOULD produce while existing rows still hold the previous answer (§31c makes the
            same argument for the price basis)
        reason: What closed it
        trade_count: How many trade records the period's figures were derived from. This is a
            CONTROL TOTAL (§48), not a statistic: it is what lets a reader re-derive the row
            from the records and find out that it does not match
        figures: The period's KPIs for ONE account currency — the same shape a whole run
            reports, so a period and a run describe themselves with one vocabulary. A unit
            trading two account currencies emits one period per currency
        period_max_equity: The highest account value seen INSIDE this period. None, with the two
            below it, on a period that could value the account at no point — a spot holding
            sealed before the first tick priced it — where a band from 0.00 to 0.00 would be a
            measurement nobody took (#476)
        period_min_equity: The lowest. Not derivable from the two above it — the peak and the
            trough belong to different moments, so `high - drawdown` is a different number
            from the low whenever the peak came after it
        period_max_drawdown: The deepest decline inside this period, against ITS OWN running
            peak. Beside it, `figures.account_max_drawdown` carries the CUMULATIVE decline of
            the whole deployment, and both are needed: the cumulative one keeps `max()` correct
            over rows, the own one answers how far this single day fell
        period_opening_equity: The account value the period OPENED with — the previous period's
            close at the same instant, or a unit's first observed value for its first period.
            None when nothing was observed. Stamped, never `final_equity - net_pnl`: net_pnl is
            realised, while the equity also values what is still open
        commission_cost / swap_cost / spread_cost: The period's costs split the way a trade row
            splits them, summed over the same trades `figures.total_fees` is — the ones the
            period CLOSED. `commission_cost + swap_cost` is `total_fees`; `spread_cost` is
            measured and stands beside it, never inside it
        modelled_spread_cost: The spread the same trades crossed against quotes the simulation
            modelled for ticks without one (#602) — beside `spread_cost`, the two adding up
        tick_count: How many ticks the period applied. Zero says the market was not heard at all
            in it — a trading day inside a data gap, booked so that every trading day has its
            row, with the valuation it was left at and no movement of its own (#476)
        trading_day: The trading day the period books, in the market's own calendar and labelled
            by the date it opened (`trading_day_anchor`); None for a unit with no market
    """
    period_no: int
    unit_name: str
    opened_at: datetime
    closed_at: datetime
    reason: PeriodCloseReason
    trade_count: int
    figures: RunSummaryCurrency
    period_max_equity: Optional[float] = 0.0
    period_min_equity: Optional[float] = 0.0
    period_max_drawdown: Optional[float] = 0.0
    period_opening_equity: Optional[float] = None
    commission_cost: float = 0.0
    swap_cost: float = 0.0
    spread_cost: float = 0.0
    modelled_spread_cost: float = 0.0
    tick_count: int = 0
    trading_day: Optional[date] = None


class FigureKind(Enum):
    """
    What kind of figure a ledger column holds — which decides whether it may cross accounts.

    A flow adds up over any rows. A ratio is never combined, only re-derived from the flows of the
    rows it describes. A reading belongs to one account — a stock, a peak, a trough, an extreme —
    and has no value over several. A description is no figure at all. A total over several
    composites carries flows only (#615).
    """
    FLOW = 'flow'
    RATIO = 'ratio'
    ACCOUNT_READING = 'account_reading'
    DESCRIPTION = 'description'


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
    MAX = 'max'             # a plain maximum: a peak, a high count. NEVER sum
    # The largest by MAGNITUDE, sign kept — for a figure whose worst value may be stored either
    # way round. A decline is the case: `account_max_drawdown` holds a magnitude,
    # `period_max_drawdown` held a negative until #539, and rows of both ages sit in one store.
    # It was `MAX` doing this silently, and the name is why: a reader who sees MAX reads `max()`
    # and is right about the columns that mean a peak and wrong about the ones that mean a fall.
    # Measured 2026-09-22 — that misreading produced a defect report against correct code, and
    # the same constant on `period_max_equity` would have picked a NEGATIVE equity over a
    # positive one, which nothing had noticed because no account has gone negative yet.
    MAX_ABS = 'max_abs'
    DERIVE = 'derive'       # a rate, a mean, a quotient: NOT combinable, re-derive from records
    # A stock read at an instant, of ONE account: the most recent row wins. Over the rows of
    # several accounts — the scenarios of one backtest — it has no value at all, because no
    # account ever held their latest readings together; the fold answers None rather than
    # whichever account happened to close last (measured 2026-09-29: a sweep row carried one
    # scenario's equity out of eight).
    LAST = 'last'
    # A stock read at the OPENING instant, of one account: the earliest ROW's value — the account
    # a unit began its window with. Unlike LAST it does not skip an unmeasured row: when the
    # earliest did not record it, the opening is unknown. Undefined over several accounts.
    FIRST = 'first'
    IDENTITY = 'identity'   # must agree across the rows, or they were never comparable
    UNION = 'union'         # a comma-joined set: combine by union, never by concatenation
    MIN = 'min'             # a plain minimum — the trough, the mirror of MAX
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

    def figure_kind(self) -> FigureKind:
        """
        The kind of figure a column with this reduction holds.

        Returns:
            Its FigureKind, declared per member in `_FIGURE_KIND_OF`
        """
        return _FIGURE_KIND_OF[self]


# The kind of figure behind each reduction (#615). Every member is listed, and a test holds the
# map to the enum, so a new reduction cannot arrive without saying whether its columns may cross
# accounts — a member missing here would raise rather than fall into a default kind.
_FIGURE_KIND_OF: Dict[Reduction, FigureKind] = {
    Reduction.SUM: FigureKind.FLOW,
    Reduction.DERIVE: FigureKind.RATIO,
    Reduction.MAX: FigureKind.ACCOUNT_READING,
    Reduction.MAX_ABS: FigureKind.ACCOUNT_READING,
    Reduction.MIN: FigureKind.ACCOUNT_READING,
    Reduction.LAST: FigureKind.ACCOUNT_READING,
    Reduction.FIRST: FigureKind.ACCOUNT_READING,
    Reduction.COMPANION: FigureKind.ACCOUNT_READING,
    Reduction.IDENTITY: FigureKind.DESCRIPTION,
    Reduction.UNION: FigureKind.DESCRIPTION,
    Reduction.SPAN_START: FigureKind.DESCRIPTION,
    Reduction.SPAN_END: FigureKind.DESCRIPTION,
}


class NullMeaning(Enum):
    """
    What the empty cell of a nullable figure means — declared per column in
    `run_results_ledger.COLUMN_NULL_MEANING`, which a test holds to the nullable figures.

    A null can mean two things, and a reader who takes one for the other decides wrongly: a
    ranking that leaves out the runs whose profit factor is undefined drops the runs that never
    lost, and one that refuses a figure merely missing from some rows refuses a question it could
    answer over the others.
    """
    # No value exists for the row: a ratio or a mean with nothing to divide by — a profit factor
    # without a losing trade — a figure about something the run did not have — a signal ratio
    # without a signal worker — or a reading of one account over the rows of several. A ranking
    # cannot place such a row, so it refuses the objective.
    UNDEFINED = 'undefined'
    # A value existed and was not recorded: a row written before its column existed, a reading
    # taken before the account's first valuation. A ranking leaves the row out and counts it.
    UNMEASURED = 'unmeasured'
