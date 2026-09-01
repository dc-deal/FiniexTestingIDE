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
from typing import List, Optional, TypeVar

from pydantic import BaseModel, ValidationError

from python.configuration.app_config_manager import AppConfigManager
from python.framework.exceptions.report_artifact_errors import ReportArtifactUnreadableError
from python.framework.reporting.io.artifact_specs import (
    ORDER_HISTORY_ARTIFACT,
    TRADE_HISTORY_ARTIFACT,
)
from python.framework.reporting.io.report_artifact_io import ArtifactSpec, read_artifact
from python.framework.reporting.io.report_filters import (
    filter_order_history_report,
    filter_trade_history_report,
)
from python.framework.reporting.store.run_index import RunIndex
from python.framework.types.api.report_types import (
    OrderHistoryReport,
    RunInfo,
    TradeHistoryReport,
)
from python.framework.types.log_layout_types import IO_SUBDIR

T = TypeVar('T', bound=BaseModel)


class ReportStore:
    """Locates + serves persisted run-report artifacts (simulation + live runs)."""

    def __init__(self, run_index_path: Optional[Path] = None):
        """
        Args:
            run_index_path: The run index to read; from config when not given. Injectable so a
                caller pointed at an isolated tree can be pointed at that tree's index too,
                rather than asking the real one about runs that only exist in tmp
        """
        self._index = RunIndex(
            run_index_path or AppConfigManager().get_file_logging_config_object().run_index)

    def list_runs(self) -> List[RunInfo]:
        """Every indexed run, both types, newest first.

        Not only runs carrying artifacts: `artifacts` says which do, and a caller that wants the
        narrower set filters on it. An index that silently omitted a type would be its own
        surprise.

        Returns:
            One identity row per run — id, run type, owning set / profile, artifacts
        """
        return self._index.list_runs()

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
