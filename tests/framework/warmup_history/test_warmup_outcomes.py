"""
Warmup Outcomes — one shortfall rule, and what each pipeline does with it (#588).

  finding                                    backtest            mock / live session
  a timeframe with no bar at all             scenario refused    refused at startup
  fewer bars, the source holds no more       warning             warning
  the venue could not be read                —                   refused (live)
  a requirement beyond bar_max_history       scenario refused    refused at startup

Before, three checks answered with three outcomes: the batch skipped an empty timeframe in
silence, the scenario process logged an error against the FIRST worker reading a timeframe,
and the session warned or refused depending on the adapter.
"""

from datetime import datetime, timezone
from typing import Dict, List
from unittest.mock import MagicMock

import pytest

from python.framework.autotrader.startup.autotrader_warmup_preparator import (
    AutotraderWarmupPreparator,
)
from python.framework.autotrader.kraken_ohlc_bar_fetcher import KrakenOhlcBarFetcher
from python.framework.bars.bar_rendering_controller import BarRenderingController
from python.framework.exceptions.connection_errors import ConnectionInadmissibleError
from python.framework.exceptions.warmup_history_errors import WarmupHistoryRefusedError
from python.framework.types.config_types.connection_policy_config_types import ConnectionPolicy
from python.framework.types.process_data_types import ProcessDataPackage, RequirementsMap
from python.framework.types.validation_types import Severity, ValidationFinding
from python.framework.validators.scenario_data_validator import ScenarioDataValidator
from tests.framework.warmup_history.warmup_history_builders import (
    SCENARIO,
    SYMBOL,
    RequirementWorker,
    VenueStub,
    archive_package,
    requirements_for,
    session_config,
)

UTC = timezone.utc
START = datetime(2026, 8, 5, 13, 2, 30, tzinfo=UTC)
NO_WAIT = ConnectionPolicy(attempt_budget=1, initial_delay_s=0.0, jitter=False)


def _validate(package: ProcessDataPackage, required: Dict[str, int],
              bar_max_history: int = 1000) -> List[ValidationFinding]:
    """The batch's data validation over one scenario's package."""
    app_config = MagicMock()
    app_config.get_bar_max_history.return_value = bar_max_history
    app_config.get_allowed_gap_categories.return_value = []
    report = MagicMock()
    report.gaps = []
    validator = ScenarioDataValidator(
        data_coverage_reports={('kraken_spot', SYMBOL): report},
        app_config=app_config, logger=MagicMock())

    scenario = MagicMock()
    scenario.name = SCENARIO
    scenario.scenario_index = 0
    scenario.symbol = SYMBOL
    scenario.data_broker_type = 'kraken_spot'
    scenario.data_sentiment_type = None
    scenario.origin_classes = []
    scenario.start_date = START
    scenario.validation_result = []
    requirements = RequirementsMap()
    for requirement in requirements_for(required, START):
        requirements.add_bar_requirement(requirement)

    validator.validate_loaded_data([scenario], {0: package}, requirements)
    return [finding for result in scenario.validation_result for finding in result.findings]


def _checks(findings: List[ValidationFinding], severity: Severity) -> List[str]:
    return sorted(finding.check for finding in findings if finding.severity is severity)


class TestTheBacktestOutcomes:

    def test_a_timeframe_without_bars_refuses_the_scenario(self, preparator):
        package = archive_package(preparator, {'H1': 3}, START, START)
        package.bars['D1'] = ()
        package.bar_counts['D1'] = 0
        findings = _validate(package, {'H1': 3, 'D1': 1})
        assert _checks(findings, Severity.ERROR) == ['warmup_history_empty']
        assert 'No warmup bars for D1' in findings[0].message

    def test_a_timeframe_missing_from_the_package_refuses_it_too(self, preparator):
        """A bar file the archive does not have: the case the batch used to skip in silence."""
        findings = _validate(archive_package(preparator, {'H1': 3}, START, START),
                             {'H1': 3, 'M10': 4})
        assert _checks(findings, Severity.ERROR) == ['warmup_history_empty']

    def test_a_short_history_warns_and_runs(self, preparator):
        package = archive_package(preparator, {'D1': 5}, START, START)
        assert package.bar_counts['D1'] == 1, 'the stream holds one complete day at the start'
        findings = _validate(package, {'D1': 5})
        assert _checks(findings, Severity.ERROR) == []
        assert _checks(findings, Severity.WARNING) == ['warmup_quality']
        assert 'D1 has 1/5 bars' in findings[0].message

    def test_a_requirement_beyond_the_bar_history_refuses_the_scenario(self, preparator):
        findings = _validate(archive_package(preparator, {'M5': 12}, START, START),
                             {'M5': 12}, bar_max_history=10)
        assert _checks(findings, Severity.ERROR) == ['warmup_capacity']


def _session(adapter: str, required: Dict[str, int], package=None, bar_max_history=1000,
             policy=None, logger=None) -> BarRenderingController:
    controller = BarRenderingController(logger=MagicMock(), max_history=bar_max_history)
    controller.register_workers([RequirementWorker(required)])
    preparator = AutotraderWarmupPreparator(logger or MagicMock(), clock=lambda: START)
    preparator.prepare_and_inject(
        config=session_config(adapter, bar_max_history), workers=[RequirementWorker(required)],
        bar_controller=controller, connection_policy=policy, package=package)
    return controller


@pytest.fixture
def venue(stream, monkeypatch) -> VenueStub:
    stub = VenueStub(stream)
    stub.now = START
    monkeypatch.setattr(KrakenOhlcBarFetcher, '_fetch_ohlc', stub.answer)
    return stub


class TestTheSessionOutcomes:

    @pytest.mark.parametrize('adapter', ['mock', 'live'])
    def test_a_requirement_beyond_the_bar_history_refuses_both_paths(
            self, adapter, preparator, venue):
        package = archive_package(preparator, {'M5': 71}, START, START)
        with pytest.raises(WarmupHistoryRefusedError, match='M5: 30/71'):
            _session(adapter, {'M5': 71}, package=package, bar_max_history=30)

    def test_an_empty_timeframe_refuses_a_mock_session(self, preparator):
        package = archive_package(preparator, {'H1': 3}, START, START)
        with pytest.raises(WarmupHistoryRefusedError, match='No warmup bars for D1: 0/1'):
            _session('mock', {'H1': 3, 'D1': 1}, package=package)

    def test_an_empty_timeframe_refuses_a_live_session(self, venue):
        venue.now = datetime(2026, 8, 4, 0, 0, 30, tzinfo=UTC)
        with pytest.raises(WarmupHistoryRefusedError, match='No warmup bars for D1'):
            _session('live', {'D1': 1}, policy=NO_WAIT)

    def test_an_unreadable_venue_refuses_a_live_session(self, venue):
        venue.failing = True
        with pytest.raises(ConnectionInadmissibleError, match='could not be read for H1'):
            _session('live', {'H1': 3}, policy=NO_WAIT)

    def test_bars_without_a_trade_are_named_in_a_live_session(self, monkeypatch):
        """
        Kraken fills an interval nobody traded with a flat row; the archive has no bar for it.
        Measured 2026-10-13: DASHUSD 187 of its last 721 M5 rows held no trade.
        """
        opens = [datetime(2026, 8, 5, hour, tzinfo=UTC) for hour in range(7, 14)]
        rows = [[int(opened.timestamp()), '1.0', '1.0', '1.0', '1.0', '1.0',
                 '0.0' if opened.hour in (9, 11) else '2.5', 0 if opened.hour in (9, 11) else 7]
                for opened in opens]
        answer = {'ETHUSD': rows, 'last': int(opens[-2].timestamp())}
        monkeypatch.setattr(KrakenOhlcBarFetcher, '_fetch_ohlc',
                            lambda self, pair, interval, since: answer)
        logger = MagicMock()

        _session('live', {'H1': 5}, policy=NO_WAIT, logger=logger)

        warnings = [call.args[0] for call in logger.warning.call_args_list]
        assert any('H1: 2 of 5 warmup bars hold no trade' in warning for warning in warnings), (
            warnings)

    def test_an_archive_history_is_never_named(self, preparator):
        """Every archive bar holds at least one tick — the archive skips an empty period."""
        logger = MagicMock()
        _session('mock', {'H1': 3}, package=archive_package(preparator, {'H1': 3}, START, START),
                 logger=logger)
        assert not any('hold no trade' in call.args[0] for call in logger.warning.call_args_list)

    @pytest.mark.parametrize('adapter', ['mock', 'live'])
    def test_a_short_history_warns_and_starts(self, adapter, preparator, venue):
        package = archive_package(preparator, {'D1': 5}, START, START)
        logger = MagicMock()
        controller = _session(adapter, {'D1': 5}, package=package, policy=NO_WAIT, logger=logger)
        assert len(controller.get_bar_history(SYMBOL, 'D1')) == 1
        warnings = [call.args[0] for call in logger.warning.call_args_list]
        assert any('D1: 1/5' in warning for warning in warnings), warnings
