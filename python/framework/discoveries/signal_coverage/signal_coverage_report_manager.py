"""
FiniexTestingIDE - Signal Coverage Report Manager

Generates signal coverage reports for batch validation — the signal-source
sibling of DataCoverageReportManager.

Responsibilities:
- Resolve the signal parquet files per (data_sentiment_type, symbol) from the index
- Generate SignalCoverageReport instances for every scenario that binds a source
- Hand the reports to ScenarioDataValidator for window validation

No cache: a report reads one projected column from a handful of parquet files,
which is cheap enough that a cache would be dead weight (unlike the tick report,
which walks a full bar file).
"""

from datetime import datetime, timezone
from typing import Dict, List, Tuple

from python.data_management.index.signal_index_manager import SignalIndexManager
from python.framework.discoveries.signal_coverage.signal_coverage_report import SignalCoverageReport
from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.types.scenario_types.scenario_set_types import (
    SignalScenarioInfo,
    SignalScenarioUsage,
    SingleScenario,
)

# Index lookup bounds — a coverage report always spans the source's full history.
_RANGE_START = datetime(1970, 1, 1, tzinfo=timezone.utc)
_RANGE_END = datetime(2100, 1, 1, tzinfo=timezone.utc)


class SignalCoverageReportManager:
    """
    Manages signal coverage report generation for batch validation.

    Reports are keyed by (data_sentiment_type, symbol) and consumed by
    ScenarioDataValidator, mirroring the tick coverage reports.
    """

    def __init__(self,
                 logger: AbstractLogger,
                 scenarios: List[SingleScenario],
                 signal_index_manager: SignalIndexManager):
        """
        Initialize signal coverage report manager.

        Args:
            logger: Logger instance
            scenarios: List of scenarios (only those binding a signal source matter)
            signal_index_manager: Signal index for file resolution
        """
        self._logger = logger
        self._scenarios = scenarios
        self._signal_index_manager = signal_index_manager
        self._reports: Dict[Tuple[str, str], SignalCoverageReport] = {}
        self._signal_scenario_map: Dict[Tuple[str, str], SignalScenarioInfo] = {}

    def generate_reports(self) -> None:
        """Generate coverage reports for all unique (data_sentiment_type, symbol) pairs."""
        pairs = set(
            (scenario.data_sentiment_type, scenario.symbol)
            for scenario in self._scenarios
            if scenario.data_sentiment_type
        )

        # Early exit: no scenario binds a signal source
        if not pairs:
            self._reports = {}
            self._signal_scenario_map = {}
            return

        reports: Dict[Tuple[str, str], SignalCoverageReport] = {}
        for sentiment_type, symbol in pairs:
            reports[(sentiment_type, symbol)] = self.build_report(
                sentiment_type, symbol)

        self._logger.info(
            f'✅ Generated {len(reports)} signal coverage report(s)')
        self._reports = reports
        self._signal_scenario_map = self._build_signal_scenario_map(reports)

    def _build_signal_scenario_map(
        self,
        reports: Dict[Tuple[str, str], SignalCoverageReport],
    ) -> Dict[Tuple[str, str], SignalScenarioInfo]:
        """
        Join each coverage report with the scenario windows bound to it (#433).

        Args:
            reports: The generated coverage reports, keyed by (source, symbol)

        Returns:
            Signal scenario map keyed by (source, symbol)
        """
        signal_map = {
            key: SignalScenarioInfo(
                data_sentiment_type=key[0], symbol=key[1], coverage=report)
            for key, report in reports.items()
        }
        for scenario in self._scenarios:
            info = signal_map.get((scenario.data_sentiment_type, scenario.symbol))
            if info is None:
                continue
            info.usages.append(SignalScenarioUsage(
                scenario_name=scenario.name,
                symbol=scenario.symbol,
                window_start=scenario.start_date,
                window_end=scenario.end_date,
            ))
        return signal_map

    def build_report(self, sentiment_type: str, symbol: str) -> SignalCoverageReport:
        """
        Build one signal coverage report.

        Public so the CLI can render a single source/symbol without a scenario
        set (construct the manager with an empty scenario list).

        Args:
            sentiment_type: Signal source identity (= pipeline_id)
            symbol: Trading symbol

        Returns:
            Analyzed SignalCoverageReport (empty when the source/symbol is unknown)
        """
        paths = self._signal_index_manager.get_relevant_files(
            sentiment_type, symbol, _RANGE_START, _RANGE_END)

        report = SignalCoverageReport(
            data_sentiment_type=sentiment_type, symbol=symbol)
        report.analyze(paths)
        return report

    def get_reports(self) -> Dict[Tuple[str, str], SignalCoverageReport]:
        """
        The generated reports, keyed by (data_sentiment_type, symbol).

        Returns:
            Report dict (empty when no scenario binds a signal source)
        """
        return self._reports

    def get_signal_scenario_map(self) -> Dict[Tuple[str, str], SignalScenarioInfo]:
        """
        Coverage + the scenario windows bound to it, keyed by (source, symbol).

        The signal sibling of the broker scenario map — the report reads it
        instead of re-opening the parquet at run end (#433).

        Returns:
            Signal scenario map (empty when no scenario binds a signal source)
        """
        return self._signal_scenario_map
