"""
FiniexTestingIDE - AutoTrader Trade Scenario Tests
Validates SL/TP close paths, duplicate signal guard, and minimal warmup
through the AutoTrader mock pipeline.

Profile calibration (BTCUSD parquet, entry bid ~89308 at tick 10):
  - SL=89200: bid drops below that level at tick ~1768
  - TP=89350: bid rises above that level at tick ~270
"""


import pytest

from python.configuration.autotrader.autotrader_config_loader import load_autotrader_config
from python.framework.autotrader.autotrader_main import AutotraderMain
from python.framework.types.log_level import LogLevel
from python.framework.types.portfolio_types.portfolio_trade_record_types import CloseReason
from tests.shared.fixture_helpers import logged_messages, remove_run_dir

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
        remove_run_dir(trader._run_dir)
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

    These tests verify the configuration path: decision sets SL → executor stores SL on
    the POSITION. They read it there rather than from a closing trade record: the MockAdapter
    does not implement broker-side SL monitoring, so nothing closes the position, and until
    #492 the observation channel was the end-of-session force-close — an exit that never
    reached the venue. The level was always on the position; reading it there needs no exit
    at all.
    """

    def test_position_opened_with_sl_level(self, sl_session):
        assert len(sl_session.open_positions) > 0, 'Expected at least one open position'
        position = sl_session.open_positions[0]
        assert position.stop_loss == 89200.0, (
            f'Expected stop_loss=89200.0 on the position, got {position.stop_loss}'
        )

    def test_position_entry_price_valid(self, sl_session):
        position = sl_session.open_positions[0]
        assert position.entry_price > 0, (
            f'Position {position.position_id}: entry_price is 0 — fill path broken'
        )

    def test_no_session_errors(self, sl_session):
        assert len(logged_messages(sl_session, LogLevel.ERROR)) == 0, (
            f'Unexpected errors: {logged_messages(sl_session, LogLevel.ERROR)}'
        )


class TestTakeProfitConfiguration:
    """
    Take profit level flows correctly through the AutoTrader pipeline.

    Same architectural note as TestStopLossConfiguration: TP triggering is
    broker-side in AutoTrader. Engine-side triggering only in simulation pipeline.

    These tests verify the configuration path: decision sets TP → executor stores TP on
    the POSITION, and it is read there (same reasoning as TestStopLossConfiguration).
    """

    def test_position_opened_with_tp_level(self, tp_session):
        assert len(tp_session.open_positions) > 0, 'Expected at least one open position'
        position = tp_session.open_positions[0]
        assert position.take_profit == 89350.0, (
            f'Expected take_profit=89350.0 on the position, got {position.take_profit}'
        )

    def test_position_entry_price_valid(self, tp_session):
        position = tp_session.open_positions[0]
        assert position.entry_price > 0, (
            f'Position {position.position_id}: entry_price is 0 — fill path broken'
        )

    def test_no_session_errors(self, tp_session):
        assert len(logged_messages(tp_session, LogLevel.ERROR)) == 0, (
            f'Unexpected errors: {logged_messages(tp_session, LogLevel.ERROR)}'
        )


class TestDuplicateSignalGuard:
    """
    Duplicate open guard: algo fires BUY every tick from tick 10 onward.
    Executor must reject all subsequent BUYs while a position is already open.

    hold_ticks=5000 exceeds max_ticks=500 — the session ends while the position is still
    open. Exactly one position must have been opened despite 490 repeated BUY signals.
    """

    def test_only_one_position_opened(self, duplicate_session):
        assert len(duplicate_session.open_positions) == 1, (
            f'Expected exactly 1 position (duplicate BUYs suppressed), '
            f'got {len(duplicate_session.open_positions)}'
        )

    def test_no_exit_is_fabricated_at_session_end(self, duplicate_session):
        """
        The session end books NO exit for the position it leaves open (#492).

        This test used to assert the opposite — it required a trade record with
        close_reason=SCENARIO_END. That record was a realised exit nobody executed: the
        close was filled locally and the asset stayed at the venue.
        """
        assert not [
            t for t in duplicate_session.trade_history
            if t.close_reason == CloseReason.SCENARIO_END
        ], 'A close was booked that never reached the venue'
        assert len(duplicate_session.open_positions) == 1

    def test_no_session_errors(self, duplicate_session):
        assert len(logged_messages(duplicate_session, LogLevel.ERROR)) == 0, (
            f'Unexpected errors: {logged_messages(duplicate_session, LogLevel.ERROR)}'
        )


class TestMinimalWarmup:
    """
    Minimal warmup: bar_max_history=30, workers cannot satisfy all warmup periods.
    Session must complete without crash — no exception, result returned.

    Workers (RSI periods=M5:14, Bollinger periods=M30:20) are starved of M30 history.
    Bollinger stays below warmup threshold for the entire 300-tick session.
    Decision logic receives empty/partial worker results and must not crash.
    """

    def test_session_completes(self, warmup_session):
        assert warmup_session is not None, 'Session did not complete'

    def test_no_fatal_errors(self, warmup_session):
        assert len(logged_messages(warmup_session, LogLevel.ERROR)) == 0, (
            f'Unexpected errors with bar_max_history=30: {logged_messages(warmup_session, LogLevel.ERROR)}'
        )

    def test_ticks_were_processed(self, warmup_session):
        assert warmup_session.ticks_processed > 0, (
            'Expected ticks to be processed — session failed before tick loop started'
        )
