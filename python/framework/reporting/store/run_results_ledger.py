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
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from python.configuration.app_config_manager import AppConfigManager
from python.framework.types.api.report_types import RunResultRow, RunSummary
from python.framework.types.run_results_types import RunProvenance

# Fixed column order — kept stable so fragments stay schema-compatible across runs.
LEDGER_COLUMNS: List[str] = [
    'param_hash', 'status', 'error', 'run_id', 'run_timestamp', 'sweep_id', 'sweep_params',
    'sweep_objective', 'sweep_maximize',
    'scenario_set_name', 'git_commit', 'git_branch', 'git_dirty',
    'decision_logic_type', 'decision_version', 'worker_versions',
    'config_snapshot', 'symbols', 'data_broker_type', 'currency',
    'net_pnl', 'expectancy', 'profit_factor', 'win_rate', 'max_drawdown',
    'total_fees', 'total_trades', 'winning_trades', 'losing_trades',
    'avg_win_r', 'avg_loss_r', 'r_trade_count',
    'orders_sent', 'orders_executed', 'orders_rejected', 'sl_tp_triggered',
]


class RunResultsLedger:
    """Append-per-run + read-all over the persistent run-results parquet dataset."""

    def __init__(self, ledger_dir: Path):
        """
        Args:
            ledger_dir: Directory holding the per-run parquet fragments
        """
        self._dir = Path(ledger_dir)

    def append(self, run_summary: RunSummary, provenance: RunProvenance) -> Path:
        """
        Write one fragment for a finished run.

        A successful run writes one `status='ok'` row per RunSummary currency. A failed run
        (`provenance.status == 'error'`, or no usable currencies) writes ONE `status='error'`
        row instead — it is recorded (provenance + sweep tag intact) but carries no KPIs, so the
        operator sees which combination failed and the analysis can exclude it. Never silently
        absent. The status/error are decided upstream from the canonical run outcome
        (`build_run_provenance`), not here.

        Args:
            run_summary: The run's cross-section KPI summary
            provenance: The run's provenance bundle (carries status + error)

        Returns:
            Path of the written fragment
        """
        if provenance.status == 'error' or not run_summary.currencies:
            rows = [self._error_row(provenance, provenance.error or 'run produced no usable data')]
        else:
            rows = [self._row(provenance, currency, run_summary)
                    for currency in run_summary.currencies]
        self._dir.mkdir(parents=True, exist_ok=True)
        # Fragment name unique per run: scenario_set_name is sweep-tagged per combination,
        # so combos that finish in the same wall-clock second never overwrite each other.
        path = self._dir / f'{provenance.scenario_set_name}_{provenance.run_id}.parquet'
        pd.DataFrame(rows, columns=LEDGER_COLUMNS).to_parquet(path, index=False)
        return path

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
        files = sorted(self._dir.glob('*.parquet')) if self._dir.exists() else []
        if not files:
            return pd.DataFrame(columns=LEDGER_COLUMNS)
        # Read each fragment with its OWN schema, then union — fragments written before a column
        # was added (schema evolution) simply lack it. Reading the directory in one shot would
        # collapse to a common schema and silently drop the newer columns. reindex pins the
        # canonical column set (missing → NaN, handled by _to_row; extra/renamed → dropped).
        df = pd.concat([pd.read_parquet(f) for f in files], ignore_index=True)
        df = df.reindex(columns=LEDGER_COLUMNS)
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
        data['worker_versions'] = _json_or(record.get('worker_versions'), {})
        data['symbols'] = _json_or(record.get('symbols'), [])
        sweep_params = _json_or(record.get('sweep_params'), None)
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
            'git_commit': p.git_commit,
            'git_branch': p.git_branch,
            'git_dirty': p.git_dirty,
            'decision_logic_type': p.decision_logic_type,
            'decision_version': p.decision_version,
            'worker_versions': json.dumps(p.worker_versions, sort_keys=True),
            'config_snapshot': p.config_snapshot,
            'symbols': json.dumps(p.symbols),
            'data_broker_type': p.data_broker_type,
        }

    def _row(self, p: RunProvenance, currency, run_summary: RunSummary) -> Dict[str, Any]:
        """An ok row: provenance + one currency's KPIs (+ run-global order counts)."""
        return {
            **self._provenance_fields(p),
            'status': 'ok',
            'error': None,
            'currency': currency.currency,
            'net_pnl': currency.net_pnl,
            'expectancy': currency.expectancy,
            'profit_factor': currency.profit_factor,
            'win_rate': currency.win_rate,
            'max_drawdown': currency.max_drawdown,
            'total_fees': currency.total_fees,
            'total_trades': currency.total_trades,
            'winning_trades': currency.winning_trades,
            'losing_trades': currency.losing_trades,
            'avg_win_r': currency.avg_win_r,
            'avg_loss_r': currency.avg_loss_r,
            'r_trade_count': currency.r_trade_count,
            'orders_sent': run_summary.orders_sent,
            'orders_executed': run_summary.orders_executed,
            'orders_rejected': run_summary.orders_rejected,
            'sl_tp_triggered': run_summary.sl_tp_triggered,
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
            if column not in row:       # all KPI / order-count columns → 0
                row[column] = 0
        return row


def append_run_to_ledger(
        run_summary: RunSummary, provenance: Optional[RunProvenance]) -> None:
    """
    Append a finished run to the persistent run-results ledger (#390).

    The shared tail both pipelines use: resolves the ledger directory from app config and
    appends the run's RunSummary + provenance. No-ops when there is no provenance (an empty
    sim batch yields none). The provenance is built pipeline-side (from_batch / from_session).

    Args:
        run_summary: The run's cross-section KPI summary
        provenance: The run's provenance bundle (None → skip)
    """
    if provenance is None:
        return
    ledger = RunResultsLedger(Path(AppConfigManager().get_run_results_path()))
    ledger.append(run_summary, provenance)


def _none_if_missing(value: Any) -> Any:
    """Normalize a raw parquet cell: pandas NaN → None, numpy scalar → Python native."""
    if value is None:
        return None
    if isinstance(value, float) and math.isnan(value):
        return None
    if hasattr(value, 'item'):      # numpy scalar (int64/float64/bool_) → native
        return value.item()
    return value


def _json_or(value: Any, default: Any) -> Any:
    """Parse a JSON-string column back to its structured value (default if null/empty)."""
    if value is None or (isinstance(value, float) and math.isnan(value)) or value == '':
        return default
    return json.loads(value)
