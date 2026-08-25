"""
Loop Cadence — Market-Data Staleness Contract (#436)

The live loop's idle heartbeat evaluates the last real tick's age against
execution.market_data_stale_after_s: edge-triggered pot warning + mandatory
on_market_data_stale dispatch + OrderGuard entry block; recovery on the next
real tick. Sim executors keep the default fresh status (replay gaps are data);
the planned stale_data_stress windows drive the same surface deterministically.
"""

import time as time_module
from datetime import datetime, timezone
from typing import Any, Dict, List
from unittest.mock import MagicMock

import pytest

from python.framework.autotrader.autotrader_tick_loop import AutotraderTickLoop
from python.framework.decision_logic.abstract_decision_logic import AbstractDecisionLogic
from python.framework.process.market_data_episode_tracker import MarketDataEpisodeTracker
from python.framework.stress_test.stale_data_stress_driver import (
    StaleDataStressDriver,
    warn_events_outside_range,
)
from python.framework.trading_env.order_guard import OrderGuard
from python.framework.types.disturbance_episode_types import DisturbanceOrigin
from python.framework.types.trading_env_types.market_data_status_types import MarketDataStatus
from python.framework.types.trading_env_types.order_types import (
    OpenOrderRequest,
    OrderDirection,
    OrderType,
    RejectionReason,
)
from python.framework.types.trading_env_types.stress_test_types import (
    StaleDataEvent,
    StressTestStaleDataConfig,
)
from python.framework.workers.worker_orchestrator import WorkerOrchestrator


def _utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


class _StubExecutor:
    """Minimal status/clock surface the loop + driver need."""

    def __init__(self, now: datetime):
        self._status = MarketDataStatus()
        self._now = now

    def set_market_data_status(self, status: MarketDataStatus) -> None:
        self._status = status

    def get_market_data_status(self) -> MarketDataStatus:
        return self._status

    def get_current_time(self) -> datetime:
        return self._now


class _HookRecorder:
    """Records on_market_data_stale invocations (duck-typed decision)."""

    def __init__(self):
        self.calls: List[MarketDataStatus] = []

    def on_market_data_stale(self, status: MarketDataStatus) -> None:
        self.calls.append(status)


class _StubTickSource:
    def __init__(self, reconnects: int = 0, injected_label: str = ''):
        self.reconnects = reconnects
        self.injected_label = injected_label

    def get_reconnect_count(self) -> int:
        return self.reconnects

    def get_injected_outage_label(self) -> str:
        return self.injected_label


def _make_loop(stale_after_s: float, last_tick_wall: float,
               now: datetime, tick_source=None):
    """Bare loop carrying only the #436/#451 state (ctor bypassed)."""
    loop = object.__new__(AutotraderTickLoop)
    loop._market_data_stale_after_s = stale_after_s
    loop._market_stale = False
    loop._market_stale_since = None
    loop._last_reconnect_count = 0
    loop._last_real_tick_wall_time = last_tick_wall
    loop._tick_source = tick_source or _StubTickSource()
    loop._executor = _StubExecutor(now)
    loop._decision_logic = _HookRecorder()
    loop._logger = MagicMock()
    loop._market_data_tracker = MarketDataEpisodeTracker(
        source='kraken_spot', logger=loop._logger, measure_wall_duration=True)
    return loop


def _observe_tick(loop, now: datetime) -> None:
    """The loop's tick-path observation, in the order run() performs it."""
    loop._executor._now = now
    if loop._market_stale:
        loop._end_market_stale_episode()
    loop._market_data_tracker.on_tick(
        now, loop._executor.get_market_data_status(), loop._injected_outage_label())


class TestLoopEvaluation:
    """Heartbeat evaluation: edge-triggered flip, escalation status, recovery."""

    def test_flip_fires_once_and_status_stays_current(self, monkeypatch):
        loop = _make_loop(300.0, last_tick_wall=1000.0, now=_utc(2026, 1, 15, 8, 0))
        monkeypatch.setattr(time_module, 'time', lambda: 1301.0)

        loop._evaluate_market_data_staleness()
        assert loop._market_stale is True
        assert loop._executor.get_market_data_status().is_stale is True
        assert len(loop._decision_logic.calls) == 1
        assert loop._decision_logic.calls[0].stale_since == _utc(2026, 1, 15, 8, 0)
        loop._logger.warning.assert_called_once()

        # Still stale — no re-fire, but the readable status keeps growing
        monkeypatch.setattr(time_module, 'time', lambda: 1400.0)
        loop._evaluate_market_data_staleness()
        assert len(loop._decision_logic.calls) == 1
        assert loop._executor.get_market_data_status().seconds_since_last_tick == 400.0

    def test_below_threshold_stays_fresh(self, monkeypatch):
        loop = _make_loop(300.0, last_tick_wall=1000.0, now=_utc(2026, 1, 15, 8, 0))
        monkeypatch.setattr(time_module, 'time', lambda: 1200.0)
        loop._evaluate_market_data_staleness()
        assert loop._market_stale is False
        assert loop._executor.get_market_data_status().is_stale is False
        assert loop._decision_logic.calls == []

    def test_disabled_contract_never_fires(self, monkeypatch):
        loop = _make_loop(0.0, last_tick_wall=1000.0, now=_utc(2026, 1, 15, 8, 0))
        monkeypatch.setattr(time_module, 'time', lambda: 99999.0)
        loop._evaluate_market_data_staleness()
        assert loop._market_stale is False
        assert loop._decision_logic.calls == []

    def test_startup_without_tick_is_not_an_outage(self, monkeypatch):
        loop = _make_loop(300.0, last_tick_wall=0.0, now=_utc(2026, 1, 15, 8, 0))
        monkeypatch.setattr(time_module, 'time', lambda: 99999.0)
        loop._evaluate_market_data_staleness()
        assert loop._market_stale is False

    def test_recovery_resets_edge_and_logs_span(self, monkeypatch):
        loop = _make_loop(300.0, last_tick_wall=1000.0, now=_utc(2026, 1, 15, 7, 55))
        # A tick at 07:55 is the last one seen as fresh — the episode anchors there.
        monkeypatch.setattr(time_module, 'time', lambda: 1000.0)
        _observe_tick(loop, _utc(2026, 1, 15, 7, 55))

        monkeypatch.setattr(time_module, 'time', lambda: 1301.0)
        loop._evaluate_market_data_staleness()

        monkeypatch.setattr(time_module, 'time', lambda: 1400.0)
        _observe_tick(loop, _utc(2026, 1, 15, 8, 5))
        assert loop._market_stale is False
        assert loop._executor.get_market_data_status().is_stale is False
        span_line = loop._logger.warning.call_args_list[-1].args[0]
        assert 'Market data recovered' in span_line
        assert '07:55:00 → 08:05:00' in span_line

        # The span is MEASURED on the wall axis, and never negative (#436 regression)
        episodes = loop._market_data_tracker.get_episodes()
        assert len(episodes) == 1
        assert episodes[0].duration_seconds == 400.0
        assert episodes[0].origin == DisturbanceOrigin.LIVE_REAL

        # Next silence flips (and notifies) again — edge was reset
        loop._last_real_tick_wall_time = 2000.0
        monkeypatch.setattr(time_module, 'time', lambda: 2301.0)
        loop._evaluate_market_data_staleness()
        assert len(loop._decision_logic.calls) == 2

    def test_episode_opens_on_the_heartbeat_and_closes_on_the_tick(self, monkeypatch):
        """The outage began when ticks stopped — not when the threshold revealed it."""
        loop = _make_loop(300.0, last_tick_wall=1000.0, now=_utc(2026, 1, 15, 8, 0))
        monkeypatch.setattr(time_module, 'time', lambda: 1000.0)
        _observe_tick(loop, _utc(2026, 1, 15, 8, 0))

        # Threshold trips two heartbeats later; no tick is counted meanwhile
        monkeypatch.setattr(time_module, 'time', lambda: 1301.0)
        loop._evaluate_market_data_staleness()
        monkeypatch.setattr(time_module, 'time', lambda: 1400.0)
        loop._evaluate_market_data_staleness()

        monkeypatch.setattr(time_module, 'time', lambda: 1500.0)
        _observe_tick(loop, _utc(2026, 1, 15, 8, 8))

        episodes = loop._market_data_tracker.get_episodes()
        assert len(episodes) == 1
        assert episodes[0].stale_from == _utc(2026, 1, 15, 8, 0)   # the last fresh tick
        assert episodes[0].stale_to == _utc(2026, 1, 15, 8, 8)
        assert loop._market_data_tracker.get_tick_stats().fresh_ticks == 2
        assert loop._market_data_tracker.get_tick_stats().stale_ticks == 0

    def test_freeze_drill_is_recorded_as_injected(self, monkeypatch):
        """The mock source declares its drill, so the episode is not read as real."""
        source = _StubTickSource(injected_label='freeze drill')
        loop = _make_loop(300.0, last_tick_wall=1000.0,
                          now=_utc(2026, 1, 15, 8, 0), tick_source=source)
        monkeypatch.setattr(time_module, 'time', lambda: 1000.0)
        _observe_tick(loop, _utc(2026, 1, 15, 8, 0))

        monkeypatch.setattr(time_module, 'time', lambda: 1301.0)
        loop._evaluate_market_data_staleness()

        source.injected_label = ''            # drill over, ticks resume
        monkeypatch.setattr(time_module, 'time', lambda: 1350.0)
        _observe_tick(loop, _utc(2026, 1, 15, 8, 6))

        episode = loop._market_data_tracker.get_episodes()[0]
        assert episode.origin == DisturbanceOrigin.STRESS_INJECTED
        assert episode.label == 'freeze drill'

    def test_reconnect_delta_reaches_the_pot(self, monkeypatch):
        source = _StubTickSource(reconnects=2)
        loop = _make_loop(300.0, last_tick_wall=1000.0,
                          now=_utc(2026, 1, 15, 8, 0), tick_source=source)
        monkeypatch.setattr(time_module, 'time', lambda: 1001.0)
        loop._evaluate_market_data_staleness()
        assert loop._last_reconnect_count == 2
        assert 'reconnected' in loop._logger.warning.call_args_list[0].args[0]
        # No re-warn without a new delta
        loop._evaluate_market_data_staleness()
        assert loop._logger.warning.call_count == 1


class TestOrderGuardStaleBlock:
    """The framework floor: no new entries on blind data (#436)."""

    def _request(self) -> OpenOrderRequest:
        return OpenOrderRequest(
            symbol='BTCUSD', order_type=OrderType.MARKET,
            direction=OrderDirection.LONG, lots=0.01)

    def test_stale_entry_rejected(self):
        guard = OrderGuard()
        result = guard.validate(
            self._request(), _utc(2026, 1, 15, 8, 0),
            MarketDataStatus(is_stale=True, seconds_since_last_tick=400.0))
        assert result is not None
        assert result.rejection_reason == RejectionReason.STALE_MARKET_DATA

    def test_fresh_entry_passes(self):
        guard = OrderGuard()
        assert guard.validate(
            self._request(), _utc(2026, 1, 15, 8, 0), MarketDataStatus()) is None

    def test_block_disabled_lets_entry_pass(self):
        guard = OrderGuard(block_stale_market_data=False)
        assert guard.validate(
            self._request(), _utc(2026, 1, 15, 8, 0),
            MarketDataStatus(is_stale=True)) is None

    def test_no_status_argument_passes(self):
        guard = OrderGuard()
        assert guard.validate(self._request(), _utc(2026, 1, 15, 8, 0)) is None

    def test_cooldown_still_applies_when_fresh(self):
        guard = OrderGuard(cooldown_seconds=60.0, max_consecutive_rejections=1)
        now = _utc(2026, 1, 15, 8, 0)
        guard.record_rejection(OrderDirection.LONG, now)
        result = guard.validate(self._request(), now, MarketDataStatus())
        assert result is not None
        assert result.rejection_reason == RejectionReason.REJECTION_COOLDOWN


class _NoHookDecision(AbstractDecisionLogic):
    """Missing on_market_data_stale — must be rejected at startup."""

    @classmethod
    def get_required_order_types(cls, decision_logic_config: Dict[str, Any]):
        return [OrderType.MARKET]

    def get_required_workers(self):
        return {}

    def compute_tick(self, tick, worker_results):
        return None

    def _execute_decision_impl(self, decision, tick):
        return None


class _CompliantDecision(_NoHookDecision):
    def on_market_data_stale(self, status: MarketDataStatus) -> None:
        pass


class TestMandatoryHookValidation:
    """EVERY decision logic must program its market-outage reaction (#436)."""

    def test_missing_override_rejected(self):
        decision = _NoHookDecision('stub', MagicMock(), {})
        with pytest.raises(ValueError, match='on_market_data_stale'):
            WorkerOrchestrator(
                workers=[], decision_logic=decision,
                strategy_config={}, parallel_workers=False)

    def test_override_accepted(self):
        decision = _CompliantDecision('stub', MagicMock(), {})
        orchestrator = WorkerOrchestrator(
            workers=[], decision_logic=decision,
            strategy_config={}, parallel_workers=False)
        assert orchestrator is not None


class TestStaleDataEventParsing:
    """stale_data_stress config: parse once, validate hard (config errors, §33)."""

    def test_missing_data_source_rejected(self):
        with pytest.raises(ValueError, match="'data_source' is required"):
            StaleDataEvent.from_dict({
                'stale_start_date': '2026-04-27T06:00:00+00:00',
                'stale_end_date': '2026-04-27T06:10:00+00:00'})

    def test_inverted_window_rejected(self):
        with pytest.raises(ValueError, match='before'):
            StaleDataEvent.from_dict({
                'data_source': 'kraken_spot',
                'stale_start_date': '2026-04-27T06:10:00+00:00',
                'stale_end_date': '2026-04-27T06:00:00+00:00'})

    def test_sources_are_filtered_and_sorted(self):
        cfg = StressTestStaleDataConfig.from_dict({'enabled': True, 'events': [
            {'label': 'b', 'data_source': 'kraken_spot',
             'stale_start_date': '2026-04-27T07:00:00+00:00',
             'stale_end_date': '2026-04-27T07:10:00+00:00'},
            {'label': 'a', 'data_source': 'kraken_spot',
             'stale_start_date': '2026-04-27T06:00:00+00:00',
             'stale_end_date': '2026-04-27T06:10:00+00:00'},
            {'label': 's', 'data_source': 'crypto_sentiment',
             'stale_start_date': '2026-04-27T06:30:00+00:00',
             'stale_end_date': '2026-04-27T07:30:00+00:00'},
        ]})
        assert [e.label for e in cfg.get_events_for_source(
            'kraken_spot')] == ['a', 'b']
        assert len(cfg.get_windows_for_source('crypto_sentiment')) == 1
        assert cfg.get_windows_for_source('other_feed') == []
        assert cfg.get_referenced_sources() == [
            'crypto_sentiment', 'kraken_spot']

    def test_disabled_config_yields_nothing(self):
        cfg = StressTestStaleDataConfig.from_dict({'enabled': False, 'events': [
            {'label': 'x', 'data_source': 'kraken_spot',
             'stale_start_date': '2026-04-27T06:00:00+00:00',
             'stale_end_date': '2026-04-27T06:10:00+00:00'}]})
        assert cfg.get_events_for_source('kraken_spot') == []
        assert cfg.get_referenced_sources() == []


def _event(label, start, end) -> StaleDataEvent:
    return StaleDataEvent(
        label=label, data_source='kraken_spot',
        stale_start_date=start, stale_end_date=end)


class TestStaleDataStressDriver:
    """Status-plane window state machine on the sim time axis."""

    def _driver(self, events):
        executor = _StubExecutor(_utc(2026, 4, 27, 6, 0))
        decision = _HookRecorder()
        logger = MagicMock()
        return StaleDataStressDriver(events, executor, decision, logger), \
            executor, decision, logger

    def _observed(self, driver, executor, logger):
        """Driver + observer wired as the sim tick loop wires them (#451)."""
        tracker = MarketDataEpisodeTracker(source='kraken_spot', logger=logger)

        def advance(now: datetime) -> None:
            driver.on_tick(now)
            tracker.on_tick(now, executor.get_market_data_status(),
                            driver.get_active_label())
        return tracker, advance

    def test_window_lifecycle(self):
        driver, executor, decision, logger = self._driver([
            _event('w1', _utc(2026, 4, 27, 6, 15), _utc(2026, 4, 27, 6, 25))])
        tracker, advance = self._observed(driver, executor, logger)

        advance(_utc(2026, 4, 27, 6, 10))
        assert executor.get_market_data_status().is_stale is False

        advance(_utc(2026, 4, 27, 6, 16))
        assert executor.get_market_data_status().is_stale is True
        assert len(decision.calls) == 1

        advance(_utc(2026, 4, 27, 6, 20))
        assert len(decision.calls) == 1  # no re-fire inside the window
        assert executor.get_market_data_status().seconds_since_last_tick == 300.0

        advance(_utc(2026, 4, 27, 6, 26))
        assert executor.get_market_data_status().is_stale is False
        recovery_line = logger.warning.call_args_list[-1].args[0]
        assert 'recovered' in recovery_line

        # The EXPERIENCED span: first stale tick 06:16 → recovery tick 06:26. The planned
        # window (06:15 → 06:25) is deliberately NOT what gets recorded.
        episode = tracker.get_episodes()[0]
        assert episode.stale_from == _utc(2026, 4, 27, 6, 16)
        assert episode.stale_to == _utc(2026, 4, 27, 6, 26)
        assert episode.origin == DisturbanceOrigin.STRESS_INJECTED
        assert episode.label == 'w1'
        assert tracker.get_tick_stats().fresh_ticks == 2
        assert tracker.get_tick_stats().stale_ticks == 2

    def test_window_outliving_the_scenario_records_what_was_experienced(self):
        """
        The case #451 was extracted for: a window reaching past the scenario end.

        Scenario 06:00 → 06:30, window 06:15 → 07:00. The run experiences 15 minutes and
        never recovers — reading the configuration instead would claim 45 minutes and a
        clean recovery that never happened.
        """
        driver, executor, decision, logger = self._driver([
            _event('w1', _utc(2026, 4, 27, 6, 15), _utc(2026, 4, 27, 7, 0))])
        tracker, advance = self._observed(driver, executor, logger)

        advance(_utc(2026, 4, 27, 6, 15))
        advance(_utc(2026, 4, 27, 6, 30))
        driver.finish()

        episodes = tracker.get_episodes(_utc(2026, 4, 27, 6, 30))
        assert len(episodes) == 1
        assert episodes[0].stale_from == _utc(2026, 4, 27, 6, 15)
        assert episodes[0].stale_to is None                  # never recovered
        assert episodes[0].duration_seconds == 900.0         # 15 min, not 45
        assert not any('recovered' in c.args[0]
                       for c in logger.warning.call_args_list)

    def test_second_window_fires_again(self):
        driver, executor, decision, _ = self._driver([
            _event('w1', _utc(2026, 4, 27, 6, 15), _utc(2026, 4, 27, 6, 20)),
            _event('w2', _utc(2026, 4, 27, 6, 30), _utc(2026, 4, 27, 6, 35))])
        driver.on_tick(_utc(2026, 4, 27, 6, 16))
        driver.on_tick(_utc(2026, 4, 27, 6, 21))
        driver.on_tick(_utc(2026, 4, 27, 6, 31))
        assert len(decision.calls) == 2
        assert executor.get_market_data_status().is_stale is True

    def test_window_without_ticks_inside_is_skipped(self):
        driver, executor, decision, logger = self._driver([
            _event('w1', _utc(2026, 4, 27, 6, 15), _utc(2026, 4, 27, 6, 20))])
        driver.on_tick(_utc(2026, 4, 27, 6, 10))
        driver.on_tick(_utc(2026, 4, 27, 6, 25))  # jumped over the whole window
        assert decision.calls == []
        assert executor.get_market_data_status().is_stale is False
        assert any('skipped' in c.args[0] for c in logger.info.call_args_list)

    def test_finish_restores_the_fresh_status(self):
        driver, executor, decision, logger = self._driver([
            _event('w1', _utc(2026, 4, 27, 6, 15), _utc(2026, 4, 27, 6, 25))])
        driver.on_tick(_utc(2026, 4, 27, 6, 16))
        driver.finish()
        assert executor.get_market_data_status().is_stale is False

    def test_overlap_guard_warns_on_disjoint_window(self):
        logger = MagicMock()
        warn_events_outside_range(
            [_event('early', _utc(2026, 4, 27, 5, 0), _utc(2026, 4, 27, 5, 10)),
             _event('inside', _utc(2026, 4, 27, 6, 15), _utc(2026, 4, 27, 6, 25))],
            data_start=_utc(2026, 4, 27, 6, 0),
            data_end=_utc(2026, 4, 27, 7, 0),
            logger=logger)
        warnings = [c.args[0] for c in logger.warning.call_args_list]
        assert len(warnings) == 1
        assert 'early' in warnings[0] and 'data deviation' in warnings[0]
