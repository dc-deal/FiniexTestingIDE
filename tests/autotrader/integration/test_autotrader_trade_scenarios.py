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
    A stop loss declared in live is enforced, and by us (#500).

    These tests used to assert the opposite state and say why: "SL/TP triggering is
    broker-side (Kraken handles it in live mode)". Nothing ever made that true — the submit
    payload had no field for a level and the engine's check skipped every non-simulation
    executor, so the level sat on the position, was displayed, and was enforced by nobody.
    The profile is named `sl_triggered_test` and nothing had ever triggered in it.

    Now the live executor evaluates the level against its own tick stream and closes through
    the real asynchronous close path. So the observable moved from "the level is stored" to
    "the level acted", which is the only version that can tell a working stop from a
    decorative one.

    Profile: LONG at tick 10 with `hold_ticks` far beyond `max_ticks`, so the strategy never
    closes it itself. Whatever closed the position was the stop.
    """

    def test_the_stop_closed_the_position(self, sl_session):
        """
        The stop acted, and the position is gone.

        Deliberately NOT asserting a count of exactly one. A live protective close is
        asynchronous, and two real properties of that path can change the count without
        changing the outcome: a close still in flight when the session ends is recorded as an
        anomaly and cleared rather than filled, and a partially filled close leaves lots that
        trigger again. Pinning the count made this test flaky in the suite runner while it
        passed on every repeat — a count is not what this scenario is here to prove.
        """
        closed = [t for t in sl_session.trade_history
                  if t.close_reason == CloseReason.SL_TRIGGERED]
        assert closed, (
            f'The stop never acted. Exit reasons seen: '
            f'{[t.close_reason for t in sl_session.trade_history]}')
        assert not sl_session.open_positions, (
            'The stop fired, so nothing may still be open on this profile')

    def test_the_exit_is_a_real_fill_and_not_the_level_itself(self, sl_session):
        """
        The live exit lands at the venue's next price, never at the level.

        This is the honest difference from the simulation, which fills a synthetic close AT
        the level. A live stop is market-on-trigger: by the time our close is confirmed the
        price has moved. Asserting the direction rather than a number keeps the test about
        the property.
        """
        exits = [t for t in sl_session.trade_history
                 if t.close_reason == CloseReason.SL_TRIGGERED]
        assert exits, 'No stop exit to inspect — see test_the_stop_closed_the_position'
        trade = exits[0]
        assert trade.entry_price > 0, (
            f'Position {trade.position_id}: entry_price is 0 — fill path broken')
        assert trade.exit_price > 0, 'A live exit carries the price it actually filled at'
        assert trade.exit_price <= 89200.0, (
            f'A LONG stop at 89200.0 cannot fill above itself, got {trade.exit_price}')

    def test_no_session_errors(self, sl_session):
        assert len(logged_messages(sl_session, LogLevel.ERROR)) == 0, (
            f'Unexpected errors: {logged_messages(sl_session, LogLevel.ERROR)}'
        )


class TestTakeProfitConfiguration:
    """
    A take profit declared in live is enforced too — the same change, the other direction.

    Same architectural note as TestStopLossConfiguration: until #500 the level was recorded
    and never acted on, and this profile's name promised a trigger that could not happen.
    """

    def test_the_target_closed_the_position(self, tp_session):
        """
        The target acted, and the position is gone.

        Deliberately NOT asserting a count of exactly one. A live protective close is
        asynchronous, and two real properties of that path can change the count without
        changing the outcome: a close still in flight when the session ends is recorded as an
        anomaly and cleared rather than filled, and a partially filled close leaves lots that
        trigger again. Pinning the count made this test flaky in the suite runner while it
        passed on every repeat — a count is not what this scenario is here to prove.
        """
        closed = [t for t in tp_session.trade_history
                  if t.close_reason == CloseReason.TP_TRIGGERED]
        assert closed, (
            f'The target never acted. Exit reasons seen: '
            f'{[t.close_reason for t in tp_session.trade_history]}')
        assert not tp_session.open_positions, (
            'The target fired, so nothing may still be open on this profile')

    def test_the_exit_is_a_real_fill_and_not_the_level_itself(self, tp_session):
        exits = [t for t in tp_session.trade_history
                 if t.close_reason == CloseReason.TP_TRIGGERED]
        assert exits, 'No target exit to inspect — see test_the_target_closed_the_position'
        trade = exits[0]
        assert trade.entry_price > 0, (
            f'Position {trade.position_id}: entry_price is 0 — fill path broken')
        assert trade.exit_price >= 89350.0, (
            f'A LONG target at 89350.0 cannot fill below itself, got {trade.exit_price}')

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
