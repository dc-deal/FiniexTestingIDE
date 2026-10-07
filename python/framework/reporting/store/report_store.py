"""
Report store (#391) — resolves persisted run-report artifacts under the run tree.

The API's read-only source: given a run id, find the run's artifact (written by either pipeline
into its run directory), read it, and — where the API filters — apply the shared filter. The
report artifacts live in the run's `io/` subfolder (`IO_SUBDIR`). A run is located through the
run index, never by walking the tree — a directory means nothing to this class (#475).

ONE typed getter serves every artifact (#486). It used to be one hand-written getter per
artifact, fifteen of them differing in three tokens each; the artifact spec carries those three
tokens, and `get(run_id, BROKER_ARTIFACT)` is still statically a `BrokerReport`.
"""

from datetime import datetime
from pathlib import Path
import json
from typing import Any, Dict, List, Optional, TypeVar

from pydantic import BaseModel, ValidationError

from python.configuration.app_config_manager import AppConfigManager
from python.framework.exceptions.report_artifact_errors import ReportArtifactUnreadableError
from python.framework.reporting.io.artifact_specs import (
    ORDER_EVENTS_STREAM,
    ORDER_HISTORY_ARTIFACT,
    TRADE_HISTORY_ARTIFACT,
)
from python.framework.reporting.io.order_event_stream_io import read_order_event_stream
from python.framework.reporting.io.report_artifact_io import ArtifactSpec, read_artifact
from python.framework.reporting.io.report_filters import (
    filter_order_history_report,
    filter_trade_history_report,
)
from python.framework.reporting.store.run_index import RunIndex
from python.framework.reporting.store.run_list_figures import get_run_list_figures
from python.framework.store.run_config_store import RunConfigStore
from python.framework.types.api.report_types import (
    OrderEventsReport,
    OrderHistoryReport,
    RunConfigSnapshot,
    RunInfo,
    RunListFigures,
    TradeHistoryReport,
)
from python.framework.types.log_layout_types import IO_SUBDIR

T = TypeVar('T', bound=BaseModel)


class ReportStore:
    """Locates + serves persisted run-report artifacts (simulation + live runs)."""

    def __init__(self, run_index_path: Optional[Path] = None, ledger_dir: Optional[Path] = None):
        """
        Args:
            run_index_path: The run index to read; from config when not given. Injectable so a
                caller pointed at an isolated tree can be pointed at that tree's index too,
                rather than asking the real one about runs that only exist in tmp
            ledger_dir: The run-results ledger the run list joins its figures from; from config
                when not given, injectable for the same reason
        """
        self._index = RunIndex(
            run_index_path or AppConfigManager().get_file_logging_config_object().run_index)
        self._ledger_dir = Path(ledger_dir or AppConfigManager().get_run_ledger_path())

    def list_runs(self) -> List[RunInfo]:
        """Every indexed run, both types, newest first.

        Not only runs carrying artifacts: `artifacts` says which do, and a caller that wants the
        narrower set filters on it. An index that silently omitted a type would be its own
        surprise.

        Returns:
            One identity row per run — id, run type, owning set / profile, artifacts
        """
        return self._index.list_runs()

    def list_runs_with_results(self) -> List[RunInfo]:
        """
        Every indexed run, with what the run-results ledger recorded it DID.

        The index says what a run IS and the ledger what it did; the two are joined here on
        `run_id`, once for the whole list. A run the ledger holds nothing for keeps `results`
        None — it is still going, died before its close, or never reported.

        Returns:
            The runs of `list_runs`, each carrying its figures where the ledger has them
        """
        figures = get_run_list_figures(self._ledger_dir)
        runs = self._index.list_runs()
        return [run.model_copy(update=_figure_fields(figures[run.run_id]))
                if run.run_id in figures else run
                for run in runs]

    def get(self, run_id: str, spec: ArtifactSpec[T]) -> Optional[T]:
        """
        Read one of a run's report artifacts.

        An artifact that is present but does not match the current model is named rather than
        allowed to escape as a bare validation failure — the usual cause is age, and an
        unexplained server error says nothing about that.

        Args:
            run_id: The run's identity
            spec: Which artifact to read; its model is what the result is typed as

        Returns:
            The decoded report, or None when the run has no such artifact
        """
        path = self._resolve(run_id, spec.filename)
        if path is None:
            return None
        try:
            return read_artifact(path, spec)
        except ValidationError as e:
            raise ReportArtifactUnreadableError(spec.filename, str(path), str(e)) from e

    def get_trade_history(
        self,
        run_id: str,
        symbol: Optional[str] = None,
        close_reason: Optional[str] = None,
        start: Optional[datetime] = None,
        end: Optional[datetime] = None,
    ) -> Optional[TradeHistoryReport]:
        """
        Read + filter a run's trade-history report.

        Filtering stays a STORE concern rather than the API's: console, file and API all filter
        through the one path, so a filtered view cannot disagree with itself between surfaces.

        Args:
            run_id: The run's identity
            symbol / close_reason / start / end: Filters (see filter_trade_history_report)

        Returns:
            The filtered report, or None if the run has no trade-history artifact
        """
        report = self.get(run_id, TRADE_HISTORY_ARTIFACT)
        if report is None:
            return None
        return filter_trade_history_report(report, symbol, close_reason, start, end)

    def get_order_history(
        self,
        run_id: str,
        symbol: Optional[str] = None,
        status: Optional[str] = None,
    ) -> Optional[OrderHistoryReport]:
        """
        Read + filter a run's order-history report.

        Args:
            run_id: The run's identity
            symbol / status: Filters (see filter_order_history_report)

        Returns:
            The filtered report, or None if the run has no order-history artifact
        """
        report = self.get(run_id, ORDER_HISTORY_ARTIFACT)
        if report is None:
            return None
        return filter_order_history_report(report, symbol, status)

    def get_order_events(
        self,
        run_id: str,
        scenario_name: Optional[str] = None,
        order_id: Optional[str] = None,
    ) -> Optional[OrderEventsReport]:
        """
        Read a run's order-event stream, optionally narrowed to one unit or one order (#362).

        Read whenever the file exists — a live session's stream grows from its first order, and
        a session that died before its report has one, which is when it is worth the most. A
        cut-off last line is left out and reported on the report rather than failing the read.

        Narrowed to one order, the broker-truth lines drop out: each one is the venue's whole
        account at a moment, never a step of one order.

        Args:
            run_id: The run's identity
            scenario_name: Keep only this unit's lines
            order_id: Keep only this order's events

        Returns:
            The report, or None when the run has no stream
        """
        path = self._resolve(run_id, ORDER_EVENTS_STREAM)
        if path is None:
            return None
        rows, truths, truncated = read_order_event_stream(path)
        if scenario_name is not None:
            rows = [row for row in rows if row.scenario_name == scenario_name]
            truths = [truth for truth in truths if truth.scenario_name == scenario_name]
        if order_id is not None:
            rows = [row for row in rows if row.order_id == order_id]
            truths = []
        return OrderEventsReport(
            run_id=run_id, events=rows, count=len(rows), broker_truth=truths,
            truncated_tail=truncated)

    def get_config_snapshot(self, run_id: str) -> Optional[RunConfigSnapshot]:
        """
        The configuration a run was commissioned with, resolved from the run-config store.

        Read from the STORE and not from the run directory. The run used to keep its own verbatim
        copy beside it, which was redundant the moment #538 began freezing the same content under
        a content id — and worse than redundant: the copy was governed by a LOGGING switch while
        the header declared it unconditionally, so a record could name a file that was not there.
        The copy is gone; `config_id` is the pointer that survives.

        Serves the frozen CONTENT and never the store's index row — that row carries
        `source_path`, an operator's own workspace path, which may name `user_configs/` or
        `user_algos/`. The content itself is clean.

        Args:
            run_id: The run's identity

        Returns:
            The configuration, or None when the run is unknown or predates the store — a run
            started before #538 carries an empty `config_id`, which is genuinely unknown rather
            than absent
        """
        info = next((r for r in self.list_runs() if r.run_id == run_id), None)
        if info is None or not info.config_id:
            return None
        frozen = RunConfigStore(
            Path(AppConfigManager().get_run_configs_path())).frozen_path_of(info.config_id)
        if frozen is None or not frozen.exists():
            return None
        return RunConfigSnapshot(
            run_id=run_id,
            config_snapshot=info.config_snapshot,
            config_id=info.config_id,
            config=json.loads(frozen.read_text(encoding='utf-8')),
        )

    def _resolve(self, run_id: str, artifact: str) -> Optional[Path]:
        """
        Find a named report artifact through the run index.

        The lookup is an EXACT match against the index, and that is the guard. The previous
        implementation interpolated the id — which arrives from a URL — into a glob pattern,
        where `'*'` is a valid-looking id that matches the first run in the tree. Membership in
        a table of known ids is strictly stronger than a shape check: a shape accepts anything
        well-formed, including ids that do not exist.

        The index also replaces the depth-dependent search this used to need: a sweep's
        combination sat one level deeper than a standalone run, so the lookup had to know the
        shape of the tree. It now looks up a row.

        Args:
            run_id: The run's identity
            artifact: The artifact's file name

        Returns:
            The artifact path, or None when the run is unknown or carries no such artifact
        """
        run_dir = self._index.run_dir(run_id)
        if run_dir is None:
            return None
        path = run_dir / IO_SUBDIR / artifact
        return path if path.exists() else None


def _figure_fields(figures: RunListFigures) -> Dict[str, Any]:
    """
    The RunInfo fields one run's ledger figures fill.

    Args:
        figures: What the ledger recorded for the run

    Returns:
        Field name → value, the nested figures kept as models
    """
    return {'results': list(figures.results), 'run_outcome': figures.run_outcome,
            'error_count': figures.error_count, 'warning_count': figures.warning_count,
            'log_warning_count': figures.log_warning_count,
            'tick_timespan_seconds': figures.tick_timespan_seconds}
