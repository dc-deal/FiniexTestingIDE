"""
FiniexTestingIDE - AutoTrader Sentiment Feed Integration Tests (#431)
End-to-end: sentiment_source profile block → index/override resolution →
provider injection → SIGNAL worker consumption in a live mock session.

Uses sentiment_mock_test.json (in-coverage replay, index-resolved) and
sentiment_outage_test.json (stale-tail replay via parquet override) with the
imported crypto_sentiment archive. Deterministic: same data + config = same results.
"""

import json
import shutil
from pathlib import Path

import pytest

from python.configuration.autotrader.autotrader_config_loader import load_autotrader_config
from python.framework.autotrader.autotrader_main import AutotraderMain
from python.framework.autotrader.autotrader_sentiment_feed import setup_sentiment_feed
from python.framework.exceptions.signal_data_errors import SignalDataUnavailableError
from python.framework.factory.worker_factory import WorkerFactory
from python.framework.logging.bootstrap_logger import get_global_logger
from python.framework.reporting.io.portfolio_report_io import PORTFOLIO_ARTIFACT, read_portfolio_report
from python.framework.reporting.store.report_store import IO_SUBDIR
from python.framework.types.config_types.market_config_types import MarketType
from python.framework.types.market_types.market_types import TradingContext


MOCK_PROFILE = 'configs/autotrader_profiles/backtesting/sentiment_mock_test.json'
OUTAGE_PROFILE = 'configs/autotrader_profiles/backtesting/sentiment_outage_test.json'
# Tick file entirely before the crypto_sentiment archive (2026-04-27 → 05-03)
NO_OVERLAP_TICK_PARQUET = 'data/processed/kraken_spot/ticks/BTCUSD/BTCUSD_20260124_141946.parquet'


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
    Run one stale-tail session: tick replay starts AFTER the sentiment archive
    ends (2026-05-04 vs. archive end 2026-05-03 23:50) — the deliberate-outage
    case, resolved via the parquet_path override.
    """
    config = load_autotrader_config(OUTAGE_PROFILE)
    trader = AutotraderMain(config)
    result = trader.run()
    yield result, trader._run_dir
    if trader._run_dir and trader._run_dir.exists():
        shutil.rmtree(trader._run_dir)


@pytest.fixture()
def sentiment_workers():
    """Workers built from the sentiment profile's strategy config (real factory path)."""
    config = load_autotrader_config(MOCK_PROFILE)
    context = TradingContext(
        broker_type='kraken_spot',
        market_type=MarketType.CRYPTO,
        symbol='BTCUSD',
        volume_min=0.00005,
    )
    factory = WorkerFactory(logger=get_global_logger())
    workers_dict = factory.create_workers_from_config(
        strategy_config=config.strategy_config,
        trading_context=context,
    )
    return list(workers_dict.values())


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
        """The persisted portfolio report tags the session's sentiment feed (#431)."""
        _, run_dir = sentiment_session
        report = read_portfolio_report(run_dir / IO_SUBDIR / PORTFOLIO_ARTIFACT)
        assert report.units[0].sentiment_source == 'crypto_sentiment', (
            f"Expected sentiment_source 'crypto_sentiment', "
            f"got '{report.units[0].sentiment_source}'"
        )


class TestSentimentOutageSession:
    """
    Deliberate-outage parity: the session runs entirely AFTER the archive end —
    the worker resolves only the aged last snapshot (is_stale) and the decision
    degrades to pure-indicator mode. The session must stay clean.
    """

    def test_stale_tail_session_completes(self, outage_session):
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

    def test_sentiment_worker_stale_signature(self, outage_session):
        """
        Outage signature: exactly ONE compute (cold start) — the resolved
        snapshot never changes because the archive ended before the session.
        """
        result, _ = outage_session
        stats = _worker_stats(result, 'sentiment')
        assert stats.worker_call_count == 1, (
            f"Expected the single cold-start compute, got {stats.worker_call_count}"
        )

    def test_outage_hook_fired(self, outage_session):
        """
        Signal-outage contract (#434): the session starts stale → the decision's
        on_signal_stale reaction fires once and surfaces in the warning pot.
        """
        result, _ = outage_session
        stale_warnings = [w for w in result.warning_messages if 'Signal feed stale' in w]
        assert len(stale_warnings) == 1, (
            f"Expected exactly one stale-feed warning, got {stale_warnings}"
        )


class TestSentimentFeedValidation:
    """Startup validation matrix for setup_sentiment_feed (§35 ABORT semantics)."""

    def test_signal_worker_without_sentiment_source(self, sentiment_workers):
        """A SIGNAL worker without a sentiment_source block must abort at startup."""
        config = load_autotrader_config(MOCK_PROFILE)
        config.sentiment_source.type = ''
        with pytest.raises(ValueError, match='sentiment_source'):
            setup_sentiment_feed(config, sentiment_workers, get_global_logger())

    def test_feed_without_signal_worker_is_noop(self):
        """A configured feed without SIGNAL workers warns (dead config) and returns."""
        config = load_autotrader_config(MOCK_PROFILE)
        setup_sentiment_feed(config, [], get_global_logger())

    def test_unknown_feed_type(self, sentiment_workers):
        """Only 'mock' is supported — live sentiment is the future event path."""
        config = load_autotrader_config(MOCK_PROFILE)
        config.sentiment_source.type = 'live'
        with pytest.raises(ValueError, match='Unknown sentiment source type'):
            setup_sentiment_feed(config, sentiment_workers, get_global_logger())

    def test_live_tick_source_rejected(self, sentiment_workers):
        """Recorded sentiment cannot be replayed against live ticks."""
        config = load_autotrader_config(MOCK_PROFILE)
        config.tick_source.type = 'kraken'
        with pytest.raises(ValueError, match='mock tick source'):
            setup_sentiment_feed(config, sentiment_workers, get_global_logger())

    def test_no_overlap_raises_unavailable(self, sentiment_workers):
        """A tick window outside the archive coverage is a data error (index path)."""
        config = load_autotrader_config(MOCK_PROFILE)
        config.tick_source.parquet_path = NO_OVERLAP_TICK_PARQUET
        with pytest.raises(SignalDataUnavailableError):
            setup_sentiment_feed(config, sentiment_workers, get_global_logger())

    def test_neither_type_nor_path(self, sentiment_workers):
        """A mock feed needs a data_sentiment_type or a parquet_path."""
        config = load_autotrader_config(MOCK_PROFILE)
        config.sentiment_source.data_sentiment_type = ''
        config.sentiment_source.parquet_path = ''
        with pytest.raises(ValueError, match='neither'):
            setup_sentiment_feed(config, sentiment_workers, get_global_logger())

    def test_feed_label_resolution(self):
        """Feed label: pipeline_id (index path), file name (override), '' (off)."""
        mock_config = load_autotrader_config(MOCK_PROFILE)
        assert mock_config.sentiment_source.get_feed_label() == 'crypto_sentiment'
        outage_config = load_autotrader_config(OUTAGE_PROFILE)
        assert outage_config.sentiment_source.get_feed_label() == \
            '2026-05-03.parquet'
        outage_config.sentiment_source.type = ''
        assert outage_config.sentiment_source.get_feed_label() == ''

    def test_loader_rejects_unknown_key(self, tmp_path):
        """Structural guard: typos in the sentiment_source block hard-fail."""
        profile = json.loads(Path(MOCK_PROFILE).read_text())
        profile['sentiment_source']['data_sentyment_type'] = 'typo'
        bad_profile = tmp_path / 'bad_profile.json'
        bad_profile.write_text(json.dumps(profile))
        with pytest.raises(ValueError, match='sentiment_source'):
            load_autotrader_config(str(bad_profile))
