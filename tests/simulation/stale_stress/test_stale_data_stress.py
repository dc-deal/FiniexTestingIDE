"""
FiniexTestingIDE - Stale-Data Stress — Simulation Pipeline (#436)

Runs the outage-probe decision logic through the backtesting pipeline with
planned stale windows (stress_test_config.stale_data_stress). Events block
DATA SOURCES the scenario binds; asserted per scenario:
- tick-source window (data_broker_type) → status-plane dispatch + guard block
- signal-source window (data_sentiment_type) → carved series driving the
  REAL #434 chain
- no-stress control → no events at all (no false positives)
- disjoint window → overlap-guard warning ("data deviation"), zero events
- unknown data_source → scenario excluded by preparation validation (§33)
"""

from pathlib import Path

import pytest

from python.configuration.app_config_manager import AppConfigManager
from python.framework.batch.batch_orchestrator import BatchOrchestrator
from python.framework.types.scenario_types.scenario_set_types import ScenarioSet
from python.scenario.scenario_config_loader import ScenarioConfigLoader


FIXTURE_SET = (
    Path(__file__).resolve().parents[3]
    / 'tests' / 'fixtures' / 'scenario_sets' / 'stale_stress' / 'stale_stress_probe.json'
)

# Scenario order in the fixture set (index-synced with process_result_list)
MARKET_STRESS = 0
SIGNAL_STRESS = 1
NO_STRESS_CONTROL = 2
OVERLAP_WARNING = 3
UNKNOWN_SOURCE = 4


@pytest.fixture(scope='module')
def summary():
    """Run the 5-scenario stress set once, shared across all tests."""
    scenario_config = ScenarioConfigLoader().load_config(str(FIXTURE_SET))
    app_config = AppConfigManager()
    scenario_set = ScenarioSet(scenario_config, app_config)
    return BatchOrchestrator(scenario_set, app_config).run()


def _received_events(summary, index: int):
    result = summary.process_result_list[index]
    assert result.success, (
        f"Scenario {index} failed: {result.error_message}")
    return result.tick_loop_results.decision_statistics.backtesting_metadata.received_events


class TestMarketDataWindow:
    """Tick-source window: status-plane dispatch + deterministic guard block."""

    def test_hook_fired_once_and_entry_blocked(self, summary):
        events = _received_events(summary, MARKET_STRESS)
        assert events.count('market_data_stale') == 1
        assert events.count('stale_entry_rejected') == 1
        assert not any(e.startswith('stale_entry_UNEXPECTED') for e in events)

    def test_signal_side_untouched(self, summary):
        """Sentiment cadence (10 min) stays under the 30 min threshold."""
        events = _received_events(summary, MARKET_STRESS)
        assert not any(e.startswith('signal_stale') for e in events)


class TestSignalSourceWindow:
    """Signal-source window: the carved series drives the REAL #434 chain."""

    def test_signal_hook_fired(self, summary):
        events = _received_events(summary, SIGNAL_STRESS)
        assert events.count('signal_stale:sentiment:llm_sentiment') == 1

    def test_market_hook_never_fires_without_a_window(self, summary):
        """The live-only proof: no tick-source event → no market dispatch in sim."""
        events = _received_events(summary, SIGNAL_STRESS)
        assert 'market_data_stale' not in events


class TestNoStressControl:
    """No stress config → no staleness events (no false positives)."""

    def test_no_events_recorded(self, summary):
        assert _received_events(summary, NO_STRESS_CONTROL) == []


class TestOverlapGuard:
    """A window disjoint from the data range warns ("data deviation") and stays inert."""

    def test_warning_in_scenario_buffer(self, summary):
        result = summary.process_result_list[OVERLAP_WARNING]
        assert result.success
        buffer_lines = [line for _, line in (result.scenario_logger_buffer or [])]
        assert any('data deviation' in line for line in buffer_lines), (
            f"No overlap warning found in scenario buffer "
            f"({len(buffer_lines)} lines)")

    def test_no_events_recorded(self, summary):
        assert _received_events(summary, OVERLAP_WARNING) == []


class TestUnknownSourceExcluded:
    """An event referencing a source the scenario does not bind → excluded (§33)."""

    def test_scenario_excluded_with_validation_error(self, summary):
        result = summary.process_result_list[UNKNOWN_SOURCE]
        assert result.success is False
        assert result.error_type == 'ValidationError'
        assert 'unknown data source' in (result.error_message or '')
        assert 'nonexistent_feed' in (result.error_message or '')
