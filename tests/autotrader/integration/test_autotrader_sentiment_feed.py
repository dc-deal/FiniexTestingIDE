"""
FiniexTestingIDE - AutoTrader Sentiment Feed Integration Tests (#438)
End-to-end: scenario_settings.data_sentiment_type → shared MountPreparer resolution →
signal series in the data package → provider injection → SIGNAL worker consumption in a
live mock session (live pipeline, mock adapter).

Uses sentiment_mock_test.json (in-coverage replay, index-resolved) and
sentiment_outage_test.json (a stale_data_stress carve makes the signal stale mid-session,
data-plane; #438/#436). Deterministic: same data + config = same results.
"""

import json
import shutil
from pathlib import Path

import pytest

from python.configuration.autotrader.autotrader_config_loader import load_autotrader_config
from python.framework.autotrader.autotrader_main import AutotraderMain
from python.framework.reporting.io.portfolio_report_io import PORTFOLIO_ARTIFACT, read_portfolio_report
from python.framework.reporting.store.report_store import IO_SUBDIR


MOCK_PROFILE = 'configs/autotrader_profiles/backtesting/sentiment_mock_test.json'
OUTAGE_PROFILE = 'configs/autotrader_profiles/backtesting/sentiment_outage_test.json'


@pytest.fixture(scope='module')
def sentiment_session():
    """
    Run one full sentiment mock session shared across all tests in this module.
    """
    config = load_autotrader_config(MOCK_PROFILE)
    trader = AutotraderMain(config)
    result = trader.run()
    yield result, trader._run_dir
    if trader._run_dir and trader._run_dir.exists():
        shutil.rmtree(trader._run_dir)


@pytest.fixture(scope='module')
def outage_session():
    """
    Run one signal-outage session: a stale_data_stress event carves a window out of the
    crypto_sentiment_mock series (data-plane), so the signal goes stale mid-session and recovers.
    """
    config = load_autotrader_config(OUTAGE_PROFILE)
    trader = AutotraderMain(config)
    result = trader.run()
    yield result, trader._run_dir
    if trader._run_dir and trader._run_dir.exists():
        shutil.rmtree(trader._run_dir)


def _worker_stats(result, worker_name: str):
    """Find one worker's performance stats in the session result."""
    for stats in result.worker_statistics or []:
        if stats.worker_name == worker_name:
            return stats
    raise AssertionError(
        f"Worker '{worker_name}' missing from worker_statistics: "
        f"{[s.worker_name for s in result.worker_statistics or []]}"
    )


class TestSentimentMockSession:
    """
    End-to-end: index-resolved sentiment feed drives the hybrid decision
    through a full mock session (live pipeline, mock adapter).
    """

    def test_full_sentiment_session(self, sentiment_session):
        """Session completes normally with the sentiment feed injected."""
        result, _ = sentiment_session

        assert result.shutdown_mode == 'normal', (
            f"Expected normal shutdown, got '{result.shutdown_mode}'"
        )
        assert result.ticks_processed == 20000, (
            f"Expected 20000 ticks, got {result.ticks_processed}"
        )

        # Clean session — no unexpected warnings or errors
        # Spot mode may leave positions open until scenario_end (no SHORT reversal)
        unexpected_warnings = [
            w for w in result.warning_messages
            if 'positions remain open' not in w
        ]
        assert len(unexpected_warnings) == 0, (
            f"Unexpected warnings: {unexpected_warnings[:5]}"
        )
        assert len(result.error_messages) == 0, (
            f"Unexpected errors: {result.error_messages[:5]}"
        )

    def test_sentiment_worker_refreshed(self, sentiment_session):
        """
        The SIGNAL worker recomputed on snapshot-window crossings — proves the
        provider was injected and the replay advanced through the archive.
        """
        result, _ = sentiment_session
        stats = _worker_stats(result, 'sentiment')
        assert stats.worker_call_count > 1, (
            f"Expected multiple snapshot-crossing computes, got {stats.worker_call_count}"
        )

    def test_portfolio_report_carries_sentiment_source(self, sentiment_session):
        """The persisted portfolio report tags the session's sentiment feed (#438)."""
        _, run_dir = sentiment_session
        report = read_portfolio_report(run_dir / IO_SUBDIR / PORTFOLIO_ARTIFACT)
        assert report.units[0].sentiment_source == 'crypto_sentiment_mock', (
            f"Expected sentiment_source 'crypto_sentiment_mock', "
            f"got '{report.units[0].sentiment_source}'"
        )


class TestSentimentOutageSession:
    """
    Signal-outage via a stale_data_stress carve: the worker computes while fresh, then the
    carved window ages the last snapshot past max_staleness → the decision degrades to
    pure-indicator mode and recovers. The session must stay clean (#434 data-plane chain).
    """

    def test_carved_session_completes(self, outage_session):
        """Stale sentiment degrades gracefully — no errors, normal shutdown."""
        result, _ = outage_session

        assert result.shutdown_mode == 'normal', (
            f"Expected normal shutdown, got '{result.shutdown_mode}'"
        )
        assert result.ticks_processed == 5000, (
            f"Expected 5000 ticks, got {result.ticks_processed}"
        )
        assert len(result.error_messages) == 0, (
            f"Unexpected errors: {result.error_messages[:5]}"
        )

    def test_sentiment_worker_computed_while_fresh(self, outage_session):
        """
        The worker recomputes on snapshot crossings while the feed is fresh, then the carved
        window stops the updates — more than the single cold-start compute.
        """
        result, _ = outage_session
        stats = _worker_stats(result, 'sentiment')
        assert stats.worker_call_count > 1, (
            f"Expected fresh-phase computes before the carve, got {stats.worker_call_count}"
        )

    def test_outage_hook_fired(self, outage_session):
        """
        Signal-outage contract (#434): the carved window ages the signal past staleness →
        the decision's on_signal_stale reaction fires once and surfaces in the warning pot.
        """
        result, _ = outage_session
        stale_warnings = [w for w in result.warning_messages if 'Signal feed stale' in w]
        assert len(stale_warnings) == 1, (
            f"Expected exactly one stale-feed warning, got {stale_warnings}"
        )


class TestScenarioSettingsValidation:
    """Structural guard on the scenario_settings block (Pydantic extra='forbid')."""

    def test_loader_rejects_unknown_key(self, tmp_path):
        """A typo in scenario_settings hard-fails at load (no silent misconfig)."""
        profile = json.loads(Path(MOCK_PROFILE).read_text())
        profile['scenario_settings']['data_sentyment_type'] = 'typo'
        bad_profile = tmp_path / 'bad_profile.json'
        bad_profile.write_text(json.dumps(profile))
        with pytest.raises(ValueError):
            load_autotrader_config(str(bad_profile))
