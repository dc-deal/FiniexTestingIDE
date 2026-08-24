"""
FiniexTestingIDE - Signal Coverage Scenario Validation Tests

Covers the two ScenarioDataValidator checks that consume a SignalCoverageReport:
- validate_signal_availability (pre-load): missing source, blind head, aged head
- _validate_signal_stretch (post-load): forbidden signal gaps where ticks flow

The tail is deliberately unchecked — a window reaching past the last snapshot is
contracted degradation (#434), not a data error.
"""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import List
from unittest.mock import MagicMock, patch

import pandas as pd

from python.framework.discoveries.data_coverage.data_coverage_report_cache import (
    DataCoverageReportCache,
)
from python.framework.discoveries.data_coverage.data_coverage_report_manager import (
    DataCoverageReportManager,
)
from python.framework.discoveries.signal_coverage.signal_coverage_report import SignalCoverageReport
from python.framework.types.market_types.market_data_types import TickTransportColumn
from python.framework.types.process_data_types import ProcessDataPackage, RequirementsMap
from python.framework.types.scenario_types.scenario_set_types import SingleScenario
from python.framework.types.signal_data_types import SIGNAL_ENVELOPE_SYMBOL, SignalParquetColumn
from python.framework.validators.scenario_data_validator import ScenarioDataValidator

CADENCE = timedelta(minutes=10)
SOURCE = 'crypto_sentiment'
SYMBOL = 'BTCUSD'


def _write_series(tmp_path: Path, moments: List[datetime],
                  origin: str = 'live') -> Path:
    """Write a minimal signal parquet carrying the snapshot timeline."""
    rows = []
    for moment in moments:
        msc = int(moment.timestamp() * 1000)
        rows.append({SignalParquetColumn.COLLECTED_MSC.value: msc,
                     SignalParquetColumn.SYMBOL.value: SIGNAL_ENVELOPE_SYMBOL,
                     SignalParquetColumn.DATA_ORIGIN.value: origin})
    path = tmp_path / 'series.parquet'
    pd.DataFrame(rows).to_parquet(path)
    return path


def _report(tmp_path: Path, moments: List[datetime],
            origin: str = 'live') -> SignalCoverageReport:
    """Build an analyzed report over a snapshot list."""
    report = SignalCoverageReport(SOURCE, SYMBOL)
    report.analyze([_write_series(tmp_path, moments, origin=origin)])
    return report


def _grid(start: datetime, count: int) -> List[datetime]:
    """Regular 10-minute snapshot grid."""
    return [start + CADENCE * i for i in range(count)]


def _scenario(start: datetime, sentiment_type: str = SOURCE,
              end: datetime = None) -> SingleScenario:
    """A minimal scenario binding a signal source."""
    return SingleScenario(
        name='sig_scenario',
        scenario_index=0,
        symbol=SYMBOL,
        data_broker_type='kraken_spot',
        start_date=start,
        end_date=end,
        data_sentiment_type=sentiment_type,
    )


def _validator(reports: dict, allowed: List[str] = None) -> ScenarioDataValidator:
    """Validator with a stubbed app config — only the two settings it reads."""
    app_config = MagicMock()
    app_config.get_warmup_quality_mode.return_value = 'standard'
    app_config.get_allowed_gap_categories.return_value = allowed or [
        'seamless', 'short', 'weekend', 'holiday']

    return ScenarioDataValidator(
        data_coverage_reports={},
        app_config=app_config,
        logger=MagicMock(),
        signal_coverage_reports=reports,
    )


def _package(first: datetime, last: datetime) -> ProcessDataPackage:
    """A package carrying just the two transport ticks the stretch check reads."""
    ticks = [
        {TickTransportColumn.TIME_MSC: int(first.timestamp() * 1000)},
        {TickTransportColumn.TIME_MSC: int(last.timestamp() * 1000)},
    ]
    return ProcessDataPackage(
        ticks={'sig_scenario': ticks},
        bars={},
        broker_configs=('kraken_spot', ()),
    )


class TestSignalAvailability:
    """validate_signal_availability — the pre-load checks."""

    def test_scenario_without_source_is_skipped(self, tmp_path):
        validator = _validator({})
        scenario = _scenario(
            datetime(2026, 7, 22, tzinfo=timezone.utc), sentiment_type='')

        errors, warnings = validator.validate_signal_availability(scenario)

        assert errors == []
        assert warnings == []

    def test_missing_source_is_an_error(self):
        validator = _validator({})
        scenario = _scenario(datetime(2026, 7, 22, tzinfo=timezone.utc))

        errors, warnings = validator.validate_signal_availability(scenario)

        assert len(errors) == 1
        assert 'not imported' in errors[0]

    def test_empty_report_is_an_error(self, tmp_path):
        report = SignalCoverageReport(SOURCE, SYMBOL)
        report.analyze([])
        validator = _validator({(SOURCE, SYMBOL): report})
        scenario = _scenario(datetime(2026, 7, 22, tzinfo=timezone.utc))

        errors, _ = validator.validate_signal_availability(scenario)

        assert len(errors) == 1

    def test_healthy_window_passes_clean(self, tmp_path):
        start = datetime(2026, 7, 22, 0, 0, tzinfo=timezone.utc)
        report = _report(tmp_path, _grid(start, 36))
        validator = _validator({(SOURCE, SYMBOL): report})

        errors, warnings = validator.validate_signal_availability(
            _scenario(start + timedelta(hours=1)))

        assert errors == []
        assert warnings == []

    def test_window_closing_before_the_series_is_an_error(self, tmp_path):
        # The concrete case: a scenario left on an old mock window after the
        # source was replaced by a later real archive.
        series_start = datetime(2026, 7, 22, 0, 0, tzinfo=timezone.utc)
        report = _report(tmp_path, _grid(series_start, 36))
        validator = _validator({(SOURCE, SYMBOL): report})

        errors, warnings = validator.validate_signal_availability(
            _scenario(datetime(2026, 5, 3, 23, 0, tzinfo=timezone.utc),
                      end=datetime(2026, 5, 4, 2, 0, tzinfo=timezone.utc)))

        assert len(errors) == 1
        assert 'closes before the source begins' in errors[0]
        assert warnings == []

    def test_open_ended_window_before_the_series_only_warns(self, tmp_path):
        # No end_date → the run reaches into the series; blind head, not fatal
        series_start = datetime(2026, 7, 22, 0, 0, tzinfo=timezone.utc)
        report = _report(tmp_path, _grid(series_start, 36))
        validator = _validator({(SOURCE, SYMBOL): report})

        errors, warnings = validator.validate_signal_availability(
            _scenario(series_start - timedelta(hours=3)))

        assert errors == []
        assert len(warnings) == 1

    def test_blind_head_warns_with_duration(self, tmp_path):
        series_start = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)
        report = _report(tmp_path, _grid(series_start, 36))
        validator = _validator({(SOURCE, SYMBOL): report})

        # Window opens two hours before the first snapshot
        errors, warnings = validator.validate_signal_availability(
            _scenario(series_start - timedelta(hours=2)))

        assert errors == []
        assert len(warnings) == 1
        assert 'no snapshot at or before' in warnings[0]
        assert '2h' in warnings[0]

    def test_aged_head_warns_when_start_sits_in_a_hole(self, tmp_path):
        start = datetime(2026, 7, 22, 0, 0, tzinfo=timezone.utc)
        before = _grid(start, 6)                                # → 00:50
        after = _grid(start + timedelta(hours=4), 6)            # 04:00 →
        report = _report(tmp_path, before + after)
        validator = _validator({(SOURCE, SYMBOL): report})

        # Window opens inside the hole — the worker resolves the 00:50 snapshot
        errors, warnings = validator.validate_signal_availability(
            _scenario(datetime(2026, 7, 22, 3, 0, tzinfo=timezone.utc)))

        assert errors == []
        assert len(warnings) == 1
        assert 'starts on a snapshot' in warnings[0]

    def test_synthetic_source_warns(self, tmp_path):
        start = datetime(2026, 7, 22, 0, 0, tzinfo=timezone.utc)
        report = _report(tmp_path, _grid(start, 36), origin='synthetic')
        validator = _validator({(SOURCE, SYMBOL): report})

        errors, warnings = validator.validate_signal_availability(
            _scenario(start + timedelta(hours=1)))

        assert errors == []          # generated data runs, it just says so
        assert len(warnings) == 1
        assert 'SYNTHETIC' in warnings[0]

    def test_stale_tail_is_not_flagged(self, tmp_path):
        # A window reaching past the series end is contracted degradation (#434)
        start = datetime(2026, 7, 22, 0, 0, tzinfo=timezone.utc)
        report = _report(tmp_path, _grid(start, 6))
        validator = _validator({(SOURCE, SYMBOL): report})

        errors, warnings = validator.validate_signal_availability(
            _scenario(start + timedelta(minutes=10)))

        assert errors == []
        assert warnings == []


class TestAvailabilityWiring:
    """
    The Phase 2 wiring: a signal finding must actually reach the scenario's
    ValidationResult — that is what excludes it from the batch (§33).
    """

    def _tick_report(self):
        """A clean tick coverage report — only the signal checks may fire."""
        tick_report = MagicMock()
        tick_report.gaps = []
        tick_report.start_time = datetime(2026, 7, 1, tzinfo=timezone.utc)
        tick_report.end_time = datetime(2026, 9, 1, tzinfo=timezone.utc)
        return tick_report

    def _run(self, scenario, report):
        """
        Drive Phase 1 → Phase 2 for one scenario with a stubbed tick report.

        Args:
            scenario: Scenario under test
            report: Signal coverage report, or None to simulate an unimported source

        Returns:
            The ValidationResult the phase appended to the scenario
        """
        app_config = MagicMock()
        app_config.get_warmup_quality_mode.return_value = 'standard'
        app_config.get_allowed_gap_categories.return_value = ['seamless', 'short']

        manager = DataCoverageReportManager(
            logger=MagicMock(),
            scenarios=[scenario],
            tick_index_manager=MagicMock(),
            app_config=app_config,
            signal_coverage_reports={(SOURCE, SYMBOL): report} if report else {},
        )

        with patch.object(DataCoverageReportCache, 'get_report',
                          return_value=self._tick_report()):
            manager.generate_reports()
            manager.validate_availability([scenario])

        return scenario.validation_result[-1]

    def test_missing_source_marks_the_scenario_invalid(self, tmp_path):
        scenario = _scenario(datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc))

        result = self._run(scenario, None)

        assert not result.is_valid
        assert 'not imported' in result.errors[0]

    def test_blind_head_warning_survives_as_a_valid_result(self, tmp_path):
        series_start = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)
        report = _report(tmp_path, _grid(series_start, 36))
        scenario = _scenario(series_start - timedelta(hours=2))

        result = self._run(scenario, report)

        assert result.is_valid          # a warning never excludes the scenario
        assert len(result.warnings) == 1
        assert 'no snapshot at or before' in result.warnings[0]

    def test_healthy_scenario_stays_clean(self, tmp_path):
        series_start = datetime(2026, 7, 22, 0, 0, tzinfo=timezone.utc)
        report = _report(tmp_path, _grid(series_start, 36))
        scenario = _scenario(series_start + timedelta(hours=1))

        result = self._run(scenario, report)

        assert result.is_valid
        assert result.warnings == []


class TestSignalStretch:
    """
    Signal gaps where ticks actually flow — exercised through the public
    validate_loaded_data path, the same call the batch makes in Phase 5.
    """

    def _report_with_hole(self, tmp_path, hole: timedelta) -> SignalCoverageReport:
        start = datetime(2026, 7, 22, 0, 0, tzinfo=timezone.utc)
        before = _grid(start, 6)
        after = _grid(before[-1] + hole, 6)
        return _report(tmp_path, before + after)

    def _run(self, report, scenario, package, allowed=None) -> List[str]:
        """
        Run the post-load validation and collect the scenario's errors.

        Args:
            report: Signal coverage report to validate against
            scenario: Scenario under test
            package: Its data package
            allowed: Allowed gap categories override

        Returns:
            Collected error messages
        """
        reports = {(SOURCE, SYMBOL): report} if report else {}
        validator = _validator(reports, allowed=allowed)

        # The tick coverage report is a separate concern here — a clean stub so
        # only the signal checks can produce findings.
        tick_report = MagicMock()
        tick_report.gaps = []
        validator._data_coverage_reports[
            (scenario.data_broker_type, scenario.symbol)] = tick_report

        validator.validate_loaded_data(
            scenarios=[scenario],
            scenario_packages={0: package},
            requirements_map=RequirementsMap(),
        )
        return [e for result in scenario.validation_result for e in result.errors]

    def test_large_gap_in_stretch_is_an_error(self, tmp_path):
        report = self._report_with_hole(tmp_path, timedelta(hours=3))
        scenario = _scenario(datetime(2026, 7, 22, 0, 0, tzinfo=timezone.utc))
        package = _package(
            datetime(2026, 7, 22, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 7, 22, 5, 0, tzinfo=timezone.utc))

        errors = self._run(report, scenario, package)

        assert len(errors) == 1
        assert 'LARGE signal gap' in errors[0]
        assert SOURCE in errors[0]
        assert 'Signal coverage in stretch' in errors[0]

    def test_short_gap_is_allowed_by_config(self, tmp_path):
        report = self._report_with_hole(tmp_path, timedelta(minutes=22))
        scenario = _scenario(datetime(2026, 7, 22, 0, 0, tzinfo=timezone.utc))
        package = _package(
            datetime(2026, 7, 22, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 7, 22, 3, 0, tzinfo=timezone.utc))

        assert self._run(report, scenario, package) == []

    def test_short_gap_errors_when_config_forbids_it(self, tmp_path):
        report = self._report_with_hole(tmp_path, timedelta(minutes=22))
        scenario = _scenario(datetime(2026, 7, 22, 0, 0, tzinfo=timezone.utc))
        package = _package(
            datetime(2026, 7, 22, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 7, 22, 3, 0, tzinfo=timezone.utc))

        errors = self._run(report, scenario, package, allowed=['seamless'])

        assert len(errors) == 1
        assert 'SHORT signal gap' in errors[0]

    def test_gap_outside_the_tick_stretch_is_ignored(self, tmp_path):
        report = self._report_with_hole(tmp_path, timedelta(hours=3))
        scenario = _scenario(datetime(2026, 7, 22, 0, 0, tzinfo=timezone.utc))
        # Ticks stop before the hole opens
        package = _package(
            datetime(2026, 7, 22, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 7, 22, 0, 40, tzinfo=timezone.utc))

        assert self._run(report, scenario, package) == []

    def test_scenario_without_source_is_skipped(self, tmp_path):
        scenario = _scenario(
            datetime(2026, 7, 22, tzinfo=timezone.utc), sentiment_type='')
        package = _package(
            datetime(2026, 7, 22, 0, 0, tzinfo=timezone.utc),
            datetime(2026, 7, 22, 5, 0, tzinfo=timezone.utc))

        assert self._run(None, scenario, package) == []
