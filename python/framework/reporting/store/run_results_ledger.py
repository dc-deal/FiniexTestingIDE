"""
Run Results Ledger (#390) — the persistent accumulating store the Parameter
Optimization system ranks over.

A flat directory of parquet fragments, ONE file per run (`<run_id>.parquet`):
parquet is immutable, so "one file per run" is the lock-free append. Read the whole
directory back as a single logical table. All identity (param_hash, sweep_id,
scenario_set_name, …) is COLUMNS, never folder structure — the free-text config name
never becomes load-bearing layout.

Row grain: one per (run × account currency) = a RunSummary currency row + the run's
provenance. The logical leading key for ranking is `param_hash`; filter by any column.
"""

import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional

import pandas as pd

from python.configuration.app_config_manager import AppConfigManager
from python.framework.exceptions.store_errors import LedgerRowUnreadableError
from python.framework.reporting.store.run_ledger_index import RunLedgerIndex
from python.framework.types.api.report_types import RunResultRow, RunSummary
from python.framework.types.run_results_types import BookingSegment, Reduction, RunProvenance

# Fixed column order — kept stable so fragments stay schema-compatible across runs.

LEDGER_COLUMNS: List[str] = [
    # Which version of the producing LOGIC wrote this row. The index stamps the same number
    # into its own metadata, but that says what the INDEX was built with — it cannot say what
    # a given ROW means. Without it a ranking cannot tell that it is comparing a drawdown
    # measured one way against one measured another, which is exactly what happened when the
    # measure changed under a stable column name (#497). Absent on fragments written before
    # this column existed, which reads back as None — unknown, never a made-up version.
    'logic_version',
    'param_hash', 'status', 'error', 'run_id', 'run_timestamp', 'sweep_id', 'sweep_params',
    'sweep_objective', 'sweep_maximize',
    # How many candidates this row's run was selected FROM (#32). The one input to the Deflated
    # Sharpe Ratio that cannot be recovered afterwards: it is a property of the SEARCH, and a
    # search leaves no other trace once it is over. Appended, so older fragments read back None —
    # which is "unknown", deliberately distinct from the 1 a run nobody swept writes.
    'trial_count',
    'scenario_set_name', 'app_version', 'git_commit', 'git_branch', 'git_dirty',
    'decision_logic_type', 'decision_version', 'worker_versions',
    'config_snapshot', 'symbols', 'data_broker_type', 'currency',
    'net_pnl', 'expectancy', 'profit_factor', 'win_rate', 'account_max_drawdown',
    # The drawdown's two companions. The PERCENTAGE cannot be re-derived from the amount
    # and the peak — it was measured against the peak standing at the time, and dividing
    # the two finished figures understates every run that recovered (#497). A live row is
    # CUMULATIVE over its deployment, so `max()` is the right reduction over a deployment's
    # rows and `sum()` would count one decline several times.
    'max_equity', 'account_max_drawdown_pct',
    # #492 — realised (net_pnl) and valued (final_equity) side by side. A ranking on
    # net_pnl alone rates a variant still HOLDING a winner below one that closed it, and
    # a run end no longer closes anything. Appended, so older fragments stay readable.
    'unrealized_pnl', 'final_equity', 'open_position_count',
    'total_fees', 'total_trades', 'winning_trades', 'losing_trades',
    # The two halves profit_factor is the quotient of. A rate cannot be folded out of two
    # rows; its components can be summed on any level, which is what makes a session's
    # profit factor recoverable from its booking segments (#537). Appended, so older
    # fragments read back as None.
    'gross_profit', 'gross_loss',
    'avg_win_r', 'avg_loss_r', 'r_trade_count', 'r_win_count', 'r_loss_count',
    'orders_sent', 'orders_executed', 'orders_rejected', 'sl_tp_triggered',
    'signal_fresh_ratio',
    # #518 — WHICH DATA this row was produced over. The same argument as `logic_version` at the
    # top of this list, one drawer over: that one says a measure may have changed under a stable
    # column name, these say the INPUT may have. A ranking across rows that read different
    # archives compares runs that are not comparable, and the thirty-day parity proof rests on
    # being able to show a live run and its backtest read the same thing. `input_plane` is what
    # keeps an empty triple honest — a live session reads a socket, so empty MEANS something
    # there and would otherwise be indistinguishable from a sim row that recorded nothing.
    # Appended, so older fragments stay readable and read back as None.
    'input_plane', 'data_format_versions', 'origin_classes', 'origin_evidence_grades',
    'input_files', 'unstamped_input_files', 'price_bases',
    # WHICH deployment this row belongs to and what the OPERATIONAL half of the
    # profile looked like (#497). The pair is what lets a reader attribute a change:
    # the rows of one deployment, and the hash that says where the parameters moved.
    'deployment_id', 'profile_hash',
    # WHICH PIPELINE produced this row — 'simulation' or 'live', from the same two constants
    # the run tree and the run index are named after (`log_layout_types`). Declared rather
    # than inferred: before it, telling a backtest from a live session meant reading
    # `input_plane`, which exists to answer a different question and is empty on everything
    # written before #518 — measured 2026-09-18, 520 of 616 rows could not say what they were.
    # The SUBTYPE is deliberately NOT a column: `sweep_id` and `deployment_id` already carry
    # it, and a second encoding of a fact is the copy that eventually disagrees (§19).
    # `RunResultRow.run_kind` derives it instead.
    'run_type',
    # WHEN this row was written, which is within seconds of when the run ENDED — the reports
    # are persisted at its close and the append is the last step. The ledger had no end of any
    # kind, and `SweepSummary.duration_s` says so in its own comment ("last - first run start,
    # no per-run end in the ledger"). Without it a deployment's gap can only be measured
    # between two STARTS, which counts the previous session's whole runtime as downtime: a
    # bot that ran 06:00-18:00 and restarted at 19:00 reads as a 13-hour gap instead of one.
    # A pure PROVENANCE stamp off the wall clock, and legitimately so (§9): it records when WE
    # wrote this, nothing decides on it, and the gap it feeds is REPORTED and never judged.
    'recorded_at_utc',
    # WHEN we deleted this row's run directory, empty while we have not. The row survives the
    # prune deliberately: a result whose evidence was removed is still a result, and dropping it
    # would rewrite history to say the run never happened (#390 — index and ledger have opposite
    # retention). What the column changes is what the row CLAIMS: with a stamp here, nobody can
    # re-derive its figures from the records, and the row says so instead of letting a reader
    # find out.
    #
    # It is a POSITIVE record of an action, never a guarantee about the other direction: a
    # directory removed by hand leaves this empty. That is why the symmetric check exists in
    # `run_completion_audit` — this column says what WE did, the check says what is THERE.
    'records_pruned_at',
    # === THE BOOKING PERIOD (#537) — what turns this ledger into a Hauptbuch ==============
    # Which run UNIT this period belongs to: a scenario in the simulation, the session in live.
    # Part of the key rather than decoration — a run's scenarios cover DIFFERENT windows, so
    # "day 1 of the run" is not a thing and only "day 1 of this unit" is.
    'unit_name',
    # The running number inside that unit, its two instants, and what closed it. A date is
    # deliberately NOT the key: it cannot express two closes on one day, and the industry books
    # more than once a day in several places (perp funding every 8 h, an intraday margin call,
    # an operator's period close). Absent on a row that books no period.
    'segment_no', 'segment_opened_at', 'segment_closed_at', 'segment_close_reason',
    # The CONTROL TOTAL (§48): how many records this period's figures were derived from. It is
    # what lets a reader re-derive the row from the records and find out that it does not match.
    'segment_trade_count',
    # The period's own equity band. `segment_min_equity` is tracked rather than derived — the
    # peak and the trough are different moments, so `peak - drawdown` answers a value that never
    # occurred. And the period's OWN drawdown sits beside the cumulative one above, because
    # neither can be recovered from the other: the cumulative keeps `max()` correct across rows,
    # the own one answers how far this single day fell.
    'segment_max_equity', 'segment_min_equity', 'segment_max_drawdown',
    # Excursion, holding time and streaks — what a period looks like beyond its net result.
    'avg_mae_winners', 'avg_mae_losers', 'avg_mfe_losers', 'largest_mae', 'largest_mfe',
    'avg_trade_duration_s', 'max_consecutive_wins', 'max_consecutive_losses',
]


# HOW each column combines when several rows are read as one — declared, not remembered.
#
# `deployment_history_summary` already carries the reasoning for ONE of these in a comment
# ("max(), never sum(): each live row carries the RUNNING decline against the inherited peak"),
# and nothing said it for the other fifty-six. A reader combining rows has to know per column,
# and the dangerous pairs look identical: `net_pnl` and `win_rate` are both aggregates, one
# sums and the other cannot be combined at all (CLAUDE.md §48).
#
# A test holds this map and LEDGER_COLUMNS to the same key set, so a column cannot be added
# without an answer.

COLUMN_REDUCTION: Dict[str, Reduction] = {
    # --- identity, provenance: must agree across the rows, or they are not comparable
    'logic_version': Reduction.IDENTITY,
    'param_hash': Reduction.IDENTITY,
    'status': Reduction.IDENTITY,
    'error': Reduction.IDENTITY,
    'run_id': Reduction.IDENTITY,
    'sweep_id': Reduction.IDENTITY,
    'sweep_params': Reduction.IDENTITY,
    'sweep_objective': Reduction.IDENTITY,
    'sweep_maximize': Reduction.IDENTITY,
    'scenario_set_name': Reduction.IDENTITY,
    'app_version': Reduction.IDENTITY,
    'git_commit': Reduction.IDENTITY,
    'git_branch': Reduction.IDENTITY,
    'git_dirty': Reduction.IDENTITY,
    'decision_logic_type': Reduction.IDENTITY,
    'decision_version': Reduction.IDENTITY,
    'worker_versions': Reduction.IDENTITY,
    'config_snapshot': Reduction.IDENTITY,
    'data_broker_type': Reduction.IDENTITY,
    'currency': Reduction.IDENTITY,
    'input_plane': Reduction.IDENTITY,
    'deployment_id': Reduction.IDENTITY,
    'profile_hash': Reduction.IDENTITY,
    'run_type': Reduction.IDENTITY,
    # Every row of one sweep was selected from the same search, so the rows agree by
    # construction. Across sweeps the figure is not combinable at all — two searches of 500 are
    # not a search of 1000, and adding them would claim a selection nobody performed.
    'trial_count': Reduction.IDENTITY,
    # Counts of what ONE run read. Identical across that run's currency rows; across RUNS they
    # are not additive, because a file two sessions both read would be counted twice.
    'input_files': Reduction.IDENTITY,
    'unstamped_input_files': Reduction.IDENTITY,

    # --- an instant, and WHICH END belongs in a combined row
    'run_timestamp': Reduction.SPAN_START,
    'recorded_at_utc': Reduction.SPAN_END,
    # An instant like the two above: over a set of rows the earliest and the latest both mean
    # something — when this history first lost evidence, and when it last did.
    'records_pruned_at': Reduction.SPAN_END,

    # --- comma-joined sets: union, never concatenation
    'symbols': Reduction.UNION,
    'data_format_versions': Reduction.UNION,
    'origin_classes': Reduction.UNION,
    'origin_evidence_grades': Reduction.UNION,
    'price_bases': Reduction.UNION,

    # --- flows and counters: the rows partition them, so adding is correct
    'net_pnl': Reduction.SUM,
    'total_fees': Reduction.SUM,
    'gross_profit': Reduction.SUM,
    'gross_loss': Reduction.SUM,
    'total_trades': Reduction.SUM,
    'winning_trades': Reduction.SUM,
    'losing_trades': Reduction.SUM,
    'r_trade_count': Reduction.SUM,
    'r_win_count': Reduction.SUM,
    'r_loss_count': Reduction.SUM,
    'orders_sent': Reduction.SUM,
    'orders_executed': Reduction.SUM,
    'orders_rejected': Reduction.SUM,
    'sl_tp_triggered': Reduction.SUM,

    # --- cumulative extrema. Summing counts one decline once per row that was still inside it.
    # The trio reduces TOGETHER: `account_max_drawdown` leads, and the other two are taken from
    # the row that won it. Reducing them separately pairs one row's trough with another's peak,
    # which is the defect #497 removed one layer down (report_aggregators.py:160-163).
    'account_max_drawdown': Reduction.MAX,
    'max_equity': Reduction.COMPANION,              # follows account_max_drawdown
    'account_max_drawdown_pct': Reduction.COMPANION,  # follows account_max_drawdown

    # --- stocks: read at an instant, so the most recent row is the answer
    'unrealized_pnl': Reduction.LAST,
    'final_equity': Reduction.LAST,
    'open_position_count': Reduction.LAST,

    # --- rates, means, quotients: NOT combinable. A difference or an average of two of these
    # is not one of these — they are re-derived over the records of the wider window
    'expectancy': Reduction.DERIVE,
    'profit_factor': Reduction.DERIVE,
    'win_rate': Reduction.DERIVE,
    'avg_win_r': Reduction.DERIVE,
    'avg_loss_r': Reduction.DERIVE,
    'signal_fresh_ratio': Reduction.DERIVE,

    # --- the booking period (#537)
    # Identical across the rows of one unit; across units it is what separates them, the same
    # way `currency` does.
    'unit_name': Reduction.IDENTITY,
    'segment_no': Reduction.SPAN_END,
    'segment_opened_at': Reduction.SPAN_START,
    'segment_closed_at': Reduction.SPAN_END,
    # The LAST period's reason is the one that says whether the books are complete: only a
    # `session_end` means nothing was left open. An earlier row's `anchor` says nothing about it.
    'segment_close_reason': Reduction.LAST,
    'segment_trade_count': Reduction.SUM,
    # The band of the widest period, and the deepest single-period decline. NOT the band or the
    # decline of the UNION — a fall that runs across a boundary is deeper than either period's
    # own, and `account_max_drawdown` above is the column that carries that.
    'segment_max_equity': Reduction.MAX,
    'segment_min_equity': Reduction.MIN,
    'segment_max_drawdown': Reduction.MAX,
    # The worst single excursion combines by magnitude; its MEANS do not.
    'largest_mae': Reduction.MAX,
    'largest_mfe': Reduction.MAX,
    'avg_mae_winners': Reduction.DERIVE,
    'avg_mae_losers': Reduction.DERIVE,
    'avg_mfe_losers': Reduction.DERIVE,
    'avg_trade_duration_s': Reduction.DERIVE,
    # DERIVE, and this is the one where the obvious answer is wrong. A streak can CROSS a
    # period boundary, so `max()` of two periods understates the truth: 2 and 3 in adjacent
    # segments can be a run of 5. It has to be re-derived over the records of the wider window.
    'max_consecutive_wins': Reduction.DERIVE,
    'max_consecutive_losses': Reduction.DERIVE,
}


# The booking-period group (#537). Named once so a row that books NO period can leave them out
# as a group rather than by luck: a blanket zero would give such a row `segment_no = 0`, which
# reads as a period rather than as the absence of one — and on the string members it is not even
# a valid value.
BOOKING_COLUMNS: List[str] = [
    'unit_name', 'segment_no', 'segment_opened_at', 'segment_closed_at',
    'segment_close_reason', 'segment_trade_count',
    'segment_max_equity', 'segment_min_equity', 'segment_max_drawdown',
]


# WHICH column a COMPANION follows. The comments beside the trio said this in prose and nothing
# could read it — so the first caller that actually reduced the columns would have had to know
# it by heart, which is precisely the state §49 exists to end. A test holds this map and the
# COMPANION entries of `COLUMN_REDUCTION` to the same key set.
COLUMN_COMPANION_OF: Dict[str, str] = {
    'max_equity': 'account_max_drawdown',
    'account_max_drawdown_pct': 'account_max_drawdown',
}


class RunResultsLedger:
    """Append-per-run + read-all over the persistent run-results parquet dataset."""

    def __init__(self, ledger_dir: Path):
        """
        Args:
            ledger_dir: Directory holding the per-run parquet fragments
        """
        self._dir = Path(ledger_dir)
        self._index = RunLedgerIndex(self._dir, LEDGER_COLUMNS)

    def append(
        self,
        run_summary: RunSummary,
        provenance: RunProvenance,
        segments: Optional[List[BookingSegment]] = None,
    ) -> Path:
        """
        Write one fragment for a finished run.

        One row per RunSummary currency, and `status` is a FLAG on that row rather than a
        reason to discard it. A run that produced NOTHING — no usable currencies — writes one
        `status='error'` row instead, so a failed combination is recorded rather than silently
        absent. The status/error are decided upstream from the canonical run outcome
        (`build_run_provenance`), not here.

        Status used to ALSO route a run to the error row, and that lost real money figures.
        Live has exactly one unit, so any uncaught exception — including one in the shutdown
        path — makes `failed_count >= total_units` and marks the whole session a total failure;
        the KPIs were then replaced by 26 literal zeros. Measured on a real-money field-study
        run: the session's own `io/run_summary.json` recorded a final equity of 71.97 USD read
        from Kraken while its ledger row said 0. The figures exist, were persisted seconds
        earlier by the same coordinator, and were thrown away on the way to the books. A
        ranking is unaffected — `optimization_analysis` filters on `status == 'ok'` — and the
        deployment history, which does not filter, now reads what happened instead of a zero
        nobody measured (CLAUDE.md §48).

        Args:
            run_summary: The run's cross-section KPI summary
            provenance: The run's provenance bundle (carries status + error)

        Returns:
            Path of the written fragment
        """
        if segments:
            # The run booked in PERIODS, so the periods ARE the rows and no aggregate row is
            # written beside them (#537). Writing both would put the same money in the same
            # column twice, and no reader can tell a summary from its own evidence: the
            # deployment history sums and would double every month, the sweep ranking sorts and
            # would see one candidate four times. The aggregate is not information — since
            # `COLUMN_REDUCTION` states how every column combines, it is derivable from these
            # rows, and a derivable copy kept beside its source is the pair that drifts (§19).
            rows = [self._segment_row(provenance, segment, run_summary) for segment in segments]
        elif not run_summary.currencies:
            rows = [self._error_row(provenance, provenance.error or 'run produced no usable data')]
        else:
            rows = [self._row(provenance, currency, run_summary)
                    for currency in run_summary.currencies]
        self._dir.mkdir(parents=True, exist_ok=True)
        # Fragment name unique per run: scenario_set_name is sweep-tagged per combination,
        # so combos that finish in the same wall-clock second never overwrite each other.
        path = self._dir / f'{provenance.scenario_set_name}_{provenance.run_id}.parquet'
        self._atomic_write(pd.DataFrame(rows, columns=LEDGER_COLUMNS), path)
        return path

    @staticmethod
    def _atomic_write(frame: pd.DataFrame, path: Path) -> None:
        """
        Write the fragment via a temp file + os.replace, so a crash never truncates one.

        While a fragment is written exactly once, at run close, writing straight onto the
        target was harmless: a torn file belonged to a run that had just died anyway. That
        stops being true the moment the same file is rewritten to add a booking segment
        (#537) — then a crash mid-write can truncate a fragment that already held COMPLETED
        segments, and nothing downstream notices: `RunLedgerIndex.rebuild()` raises on the
        read rather than reporting a corrupt fragment.

        The temp file carries a `.tmp` suffix on purpose. The index globs `*.parquet`, so a
        half-written file is invisible to it rather than merely unlikely to be caught.
        Same shape as `ColdStartStateStore._atomic_write`, which the carry-over has used
        since #355 for the same reason.

        Args:
            frame: The fragment's rows
            path: Where the finished fragment belongs
        """
        tmp_path = path.with_name(path.name + '.tmp')
        frame.to_parquet(tmp_path, index=False)
        os.replace(tmp_path, path)

    def mark_records_pruned(self, run_ids: Iterable[str]) -> int:
        """
        Record that these runs' directories are gone, on the rows that describe them.

        Called after a prune deleted the run tree. The rows STAY — a figure whose evidence was
        removed is still what that run produced, and deleting the row would rewrite the history
        to say the run never happened. What changes is what the row can claim: nothing can
        re-derive its figures from the records any more, so it says so rather than letting a
        reader discover it by following a path that is not there.

        Fragments are found by NAME (`*_<run_id>.parquet`) and then confirmed by reading the
        column, rather than by reading every fragment in the store: the prune already walks the
        whole run tree, and on this mount a file open costs 616x what it does on a local disk
        (§42). A run with no fragment is silently skipped — it never reached the ledger, which
        is the ordinary case for a session that was killed before its close.

        The index is rebuilt here and not by the caller, because the reason it goes stale is
        THIS write: the prune itself never touches a ledger fragment, so without the marking the
        index would stay valid. Rewriting a fragment moves its mtime, and `staleness_reason`
        compares exactly that.

        Args:
            run_ids: The runs whose directories were removed

        Returns:
            How many fragments were marked
        """
        wanted = {str(run_id) for run_id in run_ids}
        if not wanted or not self._dir.exists():
            return 0
        # A wall-clock stamp, and legitimately one (§9): it records when WE did something, no
        # decision hangs on it, and it is never an event time.
        stamp = datetime.now(timezone.utc).isoformat()
        marked = 0
        for run_id in sorted(wanted):
            for path in self._dir.glob(f'*_{run_id}.parquet'):
                frame = pd.read_parquet(path)
                if 'run_id' not in frame.columns or not (frame['run_id'] == run_id).any():
                    continue
                frame = frame.reindex(columns=LEDGER_COLUMNS)
                frame.loc[frame['run_id'] == run_id, 'records_pruned_at'] = stamp
                self._atomic_write(frame, path)
                marked += 1
        if marked:
            self._index.rebuild()
        return marked

    def read(
        self,
        sweep_id: Optional[str] = None,
        scenario_set_name: Optional[str] = None,
    ) -> pd.DataFrame:
        """
        Read the whole ledger as one table, optionally filtered.

        Args:
            sweep_id: Keep only rows of this sweep
            scenario_set_name: Keep only rows of this scenario set

        Returns:
            DataFrame of ledger rows (empty if the ledger does not exist yet)
        """
        if not self._index.fragments():
            return pd.DataFrame(columns=LEDGER_COLUMNS)
        if not self._index.is_valid():
            self._index.rebuild()
        df = self._index.read()
        if sweep_id is not None:
            df = df[df['sweep_id'] == sweep_id]
        if scenario_set_name is not None:
            df = df[df['scenario_set_name'] == scenario_set_name]
        return df.reset_index(drop=True)

    def read_rows(
        self,
        sweep_id: Optional[str] = None,
        scenario_set_name: Optional[str] = None,
    ) -> List[RunResultRow]:
        """
        Read the ledger as typed rows (the JSON columns parsed back to structured types).

        Args:
            sweep_id: Keep only rows of this sweep
            scenario_set_name: Keep only rows of this scenario set

        Returns:
            Typed ledger rows — what the optimization analysis + the API consume
        """
        return [self._to_row(record)
                for record in self.read(sweep_id, scenario_set_name).to_dict('records')]

    def _to_row(self, record: Dict[str, Any]) -> RunResultRow:
        """Build a typed RunResultRow from a raw parquet record (parse the JSON columns)."""
        # Drop None/missing cells so the model's field defaults apply — a fragment from before a
        # column existed (schema evolution) leaves that column NaN → must not override the default
        # (e.g. an int field would reject None).
        data = {k: v for k, v in ((k, _none_if_missing(v)) for k, v in record.items())
                if v is not None}
        # The run names itself in a parse failure. The ledger is read as ONE table, so a
        # single malformed cell stopped every reader with a bare JSONDecodeError that said
        # nothing about WHICH row — and with 564 fragments that is a search rather than a
        # fix. The fault is still raised rather than defaulted away: a books reader that
        # silently substitutes a value for an unreadable one is worse than one that stops.
        run_id = str(record.get('run_id', '<unknown>'))
        data['worker_versions'] = _json_or(record.get('worker_versions'), {}, run_id, 'worker_versions')
        data['symbols'] = _json_or(record.get('symbols'), [], run_id, 'symbols')
        sweep_params = _json_or(record.get('sweep_params'), None, run_id, 'sweep_params')
        if sweep_params is not None:
            data['sweep_params'] = sweep_params
        return RunResultRow(**data)

    def _provenance_fields(self, p: RunProvenance) -> Dict[str, Any]:
        """The shared provenance columns (identical for ok + error rows)."""
        return {
            'param_hash': p.param_hash,
            'run_id': p.run_id,
            'run_timestamp': p.run_timestamp.isoformat(),
            'sweep_id': p.sweep_id,
            'sweep_params': json.dumps(p.sweep_params, sort_keys=True) if p.sweep_params else None,
            'sweep_objective': p.sweep_objective,
            'sweep_maximize': p.sweep_maximize,
            'scenario_set_name': p.scenario_set_name,
            'app_version': p.app_version,
            'git_commit': p.git_commit,
            'git_branch': p.git_branch,
            'git_dirty': p.git_dirty,
            'decision_logic_type': p.decision_logic_type,
            'decision_version': p.decision_version,
            'worker_versions': json.dumps(p.worker_versions, sort_keys=True),
            'config_snapshot': p.config_snapshot,
            'symbols': json.dumps(p.symbols),
            'data_broker_type': p.data_broker_type,
            'input_plane': p.input_plane,
            'data_format_versions': p.data_format_versions,
            'origin_classes': p.origin_classes,
            'origin_evidence_grades': p.origin_evidence_grades,
            'input_files': p.input_files,
            'unstamped_input_files': p.unstamped_input_files,
            'price_bases': p.price_bases,
            'deployment_id': p.deployment_id,
            'profile_hash': p.profile_hash,
            'run_type': p.run_type,
            'trial_count': p.trial_count,
            # Empty at birth: the records exist, because the run that wrote this row just
            # produced them. Only `mark_records_pruned` ever fills it.
            'records_pruned_at': '',
            'recorded_at_utc': datetime.now(timezone.utc).isoformat(),
            'logic_version': RunLedgerIndex.LOGIC_VERSION,
        }

    def _row(self, p: RunProvenance, currency, run_summary: RunSummary) -> Dict[str, Any]:
        """
        A KPI row: provenance + one currency's figures (+ run-global order counts).

        `status` and `error` come from the PROVENANCE and are not hardcoded to 'ok'. A run
        that errored still produced these figures, and the row carries both — the numbers and
        the fact that the run did not finish cleanly. Hardcoding 'ok' here would hand a failed
        session to `optimization_analysis`, which admits a row into a ranking on exactly that
        column.

        Args:
            p: The run's provenance, including its status
            currency: One RunSummary currency row
            run_summary: The run's summary, for the run-global order counts

        Returns:
            The row as a column → value mapping
        """
        return {
            **self._provenance_fields(p),
            'status': p.status,
            'error': p.error,
            'currency': currency.currency,
            'net_pnl': currency.net_pnl,
            'expectancy': currency.expectancy,
            'profit_factor': currency.profit_factor,
            'win_rate': currency.win_rate,
            'account_max_drawdown': currency.account_max_drawdown,
            'max_equity': currency.max_equity,
            'account_max_drawdown_pct': currency.account_max_dd_pct,
            'unrealized_pnl': currency.unrealized_pnl,
            'final_equity': currency.final_equity,
            'open_position_count': currency.open_position_count,
            'total_fees': currency.total_fees,
            'gross_profit': currency.gross_profit,
            'gross_loss': currency.gross_loss,
            'total_trades': currency.total_trades,
            'winning_trades': currency.winning_trades,
            'losing_trades': currency.losing_trades,
            'avg_win_r': currency.avg_win_r,
            'avg_loss_r': currency.avg_loss_r,
            'r_trade_count': currency.r_trade_count,
            'r_win_count': currency.r_win_count,
            'r_loss_count': currency.r_loss_count,
            'orders_sent': run_summary.orders_sent,
            'orders_executed': run_summary.orders_executed,
            'orders_rejected': run_summary.orders_rejected,
            'sl_tp_triggered': run_summary.sl_tp_triggered,
            # Data quality the row was produced under (#433): a ranking over rows with
            # different fresh ratios compares runs that saw different signal.
            'signal_fresh_ratio': run_summary.signal_fresh_ratio,
        }

    def _segment_row(
        self,
        p: RunProvenance,
        segment: BookingSegment,
        run_summary: RunSummary,
    ) -> Dict[str, Any]:
        """
        One booking period as a ledger row — the Hauptbuch entry.

        The order counters are deliberately ABSENT rather than zero. They live as monotonic
        totals on the executor with no time argument, so a period's share of them cannot be
        derived without differencing two readings — which §48 forbids, and which would be the
        one measure here that silently looks right because it happens to be additive. #476's
        sealed records are what will make them windowable; until then the column says
        "not measured" and means it.

        Args:
            p: The run's provenance
            segment: The sealed period
            run_summary: The run's summary, for the figures that belong to the RUN rather than
                to any one period

        Returns:
            The row as a column → value mapping
        """
        f = segment.figures
        return {
            **self._provenance_fields(p),
            'status': p.status,
            'error': p.error,
            'currency': f.currency,
            'net_pnl': f.net_pnl,
            'expectancy': f.expectancy,
            'profit_factor': f.profit_factor,
            'win_rate': f.win_rate,
            'account_max_drawdown': f.account_max_drawdown,
            'max_equity': f.max_equity,
            'account_max_drawdown_pct': f.account_max_dd_pct,
            'unrealized_pnl': f.unrealized_pnl,
            'final_equity': f.final_equity,
            'open_position_count': f.open_position_count,
            'total_fees': f.total_fees,
            'gross_profit': f.gross_profit,
            'gross_loss': f.gross_loss,
            'total_trades': f.total_trades,
            'winning_trades': f.winning_trades,
            'losing_trades': f.losing_trades,
            'avg_win_r': f.avg_win_r,
            'avg_loss_r': f.avg_loss_r,
            'r_trade_count': f.r_trade_count,
            'r_win_count': f.r_win_count,
            'r_loss_count': f.r_loss_count,
            'avg_mae_winners': f.avg_mae_winners,
            'avg_mae_losers': f.avg_mae_losers,
            'avg_mfe_losers': f.avg_mfe_losers,
            'largest_mae': f.largest_mae,
            'largest_mfe': f.largest_mfe,
            'avg_trade_duration_s': f.avg_trade_duration_s,
            'max_consecutive_wins': f.max_consecutive_wins,
            'max_consecutive_losses': f.max_consecutive_losses,
            # Run-level and therefore identical on every period of this run: the data quality
            # the whole run saw is not a property of one day in it.
            'signal_fresh_ratio': run_summary.signal_fresh_ratio,
            # --- the period itself
            'unit_name': segment.unit_name,
            'segment_no': segment.segment_no,
            'segment_opened_at': segment.opened_at.isoformat(),
            'segment_closed_at': segment.closed_at.isoformat(),
            'segment_close_reason': segment.reason.value,
            'segment_trade_count': segment.trade_count,
            'segment_max_equity': segment.segment_max_equity,
            'segment_min_equity': segment.segment_min_equity,
            'segment_max_drawdown': segment.segment_max_drawdown,
        }

    def _error_row(self, p: RunProvenance, error: str) -> Dict[str, Any]:
        """An error row: provenance + the failure reason; no usable KPIs (all zero/empty)."""
        row = {
            **self._provenance_fields(p),
            'status': 'error',
            'error': error,
            'currency': '',
        }
        for column in LEDGER_COLUMNS:
            if column in BOOKING_COLUMNS:
                continue                # books no period — absent, never a period zero
            if column not in row:       # all KPI / order-count columns → 0
                row[column] = 0
        return row


def append_run_to_ledger(
        run_summary: RunSummary,
        provenance: Optional[RunProvenance],
        segments: Optional[List[BookingSegment]] = None) -> None:
    """
    Append a finished run to the persistent run-results ledger (#390).

    The shared tail both pipelines use: resolves the ledger directory from app config and
    appends the run's RunSummary + provenance. No-ops when there is no provenance (an empty
    sim batch yields none). The provenance is built pipeline-side (from_batch / from_session).

    Args:
        run_summary: The run's cross-section KPI summary
        provenance: The run's provenance bundle (None → skip)
        segments: The run's booking periods, flattened across its units (#537). When present
            they ARE the rows and no aggregate row is written; when absent the run books once,
            as it always did
    """
    if provenance is None:
        return
    ledger = RunResultsLedger(Path(AppConfigManager().get_run_ledger_path()))
    ledger.append(run_summary, provenance, segments)


def _none_if_missing(value: Any) -> Any:
    """Normalize a raw parquet cell: pandas NaN → None, numpy scalar → Python native."""
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if hasattr(value, 'item'):      # numpy scalar (int64/float64/bool_) → native
        return value.item()
    return value


def _json_or(value: Any, default: Any, run_id: str = '<unknown>', column: str = '') -> Any:
    """
    Parse a JSON-string column back to its structured value (default if null/empty).

    Args:
        value: The raw cell
        default: What an absent or empty cell means
        run_id: The row's run, so a failure names itself
        column: Which column failed

    Returns:
        The parsed structure, or the default
    """
    if value is None or (isinstance(value, float) and math.isnan(value)) or value == '':
        return default
    try:
        return json.loads(value)
    except (json.JSONDecodeError, TypeError) as exc:
        raise LedgerRowUnreadableError(run_id, column, str(exc)) from exc
