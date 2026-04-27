"""
FiniexTestingIDE - AutoTrader Trade Scenario Tests
Validates SL/TP close paths, duplicate signal guard, and minimal warmup
through the AutoTrader mock pipeline.

Profile calibration (BTCUSD parquet, entry bid ~89308 at tick 10):
  - SL=89200: bid drops below that level at tick ~1768
  - TP=89350: bid rises above that level at tick ~270
"""

import shutil

import pytest

from python.configuration.autotrader.autotrader_config_loader import load_autotrader_config
from python.framework.autotrader.autotrader_main import AutotraderMain
from python.framework.types.portfolio_types.portfolio_trade_record_types import CloseReason


_PROFILE_SL = 'configs/autotrader_profiles/backtesting/sl_triggered_test.json'
_PROFILE_TP = 'configs/autotrader_profiles/backtesting/tp_triggered_test.json'
_PROFILE_DUPLICATE = 'configs/autotrader_profiles/backtesting/duplicate_signal_guard_test.json'
_PROFILE_WARMUP = 'configs/autotrader_profiles/backtesting/minimal_warmup_test.json'


def _make_session_fixture(profile: str):
    """Factory for module-scoped session fixtures from a profile path."""
    @pytest.fixture(scope='module')
    def _fixture():
        config = load_autotrader_config(profile)
        trader = AutotraderMain(config)
        result = trader.run()
        yield result
        if trader._run_dir and trader._run_dir.exists():
            shutil.rmtree(trader._run_dir)
    return _fixture


sl_session = _make_session_fixture(_PROFILE_SL)
tp_session = _make_session_fixture(_PROFILE_TP)
duplicate_session = _make_session_fixture(_PROFILE_DUPLICATE)
warmup_session = _make_session_fixture(_PROFILE_WARMUP)


class TestStopLossConfiguration:
    """
    Stop loss level flows correctly through the AutoTrader pipeline.

    In the AutoTrader, SL/TP triggering is broker-side (Kraken handles it in
    live mode). Engine-side SL/TP monitoring runs only in the simulation pipeline
    (TradeSimulator, ExecutorMode.SIMULATION). LiveTradeExecutor uses LIVE mode
    and relies on the broker.

    These tests verify the configuration path: decision sets SL → executor stores
    SL on position → TradeRecord captures SL. The position closes via SCENARIO_END
    because MockAdapter does not implement broker-side SL monitoring.
    """

    def test_trade_opened_with_sl_level(self, sl_session):
        assert len(sl_session.trade_history) > 0, 'Expected at least one trade'
        trade = sl_session.trade_history[0]
        assert trade.stop_loss == 89200.0, (
            f'Expected stop_loss=89200.0 on trade record, got {trade.stop_loss}'
        )

    def test_trade_entry_price_valid(self, sl_session):
        trade = sl_session.trade_history[0]
        assert trade.entry_price > 0, (
            f'Trade {trade.position_id}: entry_price is 0 — fill path broken'
        )

    def test_no_session_errors(self, sl_session):
        assert len(sl_session.error_messages) == 0, (
            f'Unexpected errors: {sl_session.error_messages}'
        )


class TestTakeProfitConfiguration:
    """
    Take profit level flows correctly through the AutoTrader pipeline.

    Same architectural note as TestStopLossConfiguration: TP triggering is
    broker-side in AutoTrader. Engine-side triggering only in simulation pipeline.

    These tests verify the configuration path: decision sets TP → executor stores
    TP on position → TradeRecord captures TP.
    """

    def test_trade_opened_with_tp_level(self, tp_session):
        assert len(tp_session.trade_history) > 0, 'Expected at least one trade'
        trade = tp_session.trade_history[0]
        assert trade.take_profit == 89350.0, (
            f'Expected take_profit=89350.0 on trade record, got {trade.take_profit}'
        )

    def test_trade_entry_price_valid(self, tp_session):
        trade = tp_session.trade_history[0]
        assert trade.entry_price > 0, (
            f'Trade {trade.position_id}: entry_price is 0 — fill path broken'
        )

    def test_no_session_errors(self, tp_session):
        assert len(tp_session.error_messages) == 0, (
            f'Unexpected errors: {tp_session.error_messages}'
        )


class TestDuplicateSignalGuard:
    """
    Duplicate open guard: algo fires BUY every tick from tick 10 onward.
    Executor must reject all subsequent BUYs while a position is already open.

    hold_ticks=5000 exceeds max_ticks=500 — session ends with SCENARIO_END.
    Exactly one position must have been opened despite 490 repeated BUY signals.
    """

    def test_only_one_trade_opened(self, duplicate_session):
        assert len(duplicate_session.trade_history) == 1, (
            f'Expected exactly 1 trade (duplicate BUYs suppressed), '
            f'got {len(duplicate_session.trade_history)}'
        )

    def test_close_reason_is_scenario_end(self, duplicate_session):
        trade = duplicate_session.trade_history[0]
        assert trade.close_reason == CloseReason.SCENARIO_END, (
            f'Expected SCENARIO_END for position open at session end, '
            f'got {trade.close_reason}'
        )

    def test_no_session_errors(self, duplicate_session):
        assert len(duplicate_session.error_messages) == 0, (
            f'Unexpected errors: {duplicate_session.error_messages}'
        )


class TestMinimalWarmup:
    """
    Minimal warmup: bar_max_history=30, workers cannot satisfy all warmup periods.
    Session must complete without crash — no exception, result returned.

    Workers (RSI periods=M5:14, Envelope periods=M30:20) are starved of M30 history.
    Envelope stays below warmup threshold for the entire 300-tick session.
    Decision logic receives empty/partial worker results and must not crash.
    """

    def test_session_completes(self, warmup_session):
        assert warmup_session is not None, 'Session did not complete'

    def test_no_fatal_errors(self, warmup_session):
        assert len(warmup_session.error_messages) == 0, (
            f'Unexpected errors with bar_max_history=30: {warmup_session.error_messages}'
        )

    def test_ticks_were_processed(self, warmup_session):
        assert warmup_session.ticks_processed > 0, (
            'Expected ticks to be processed — session failed before tick loop started'
        )
