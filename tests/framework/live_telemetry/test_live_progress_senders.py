"""
FiniexTestingIDE - Live Progress Senders (#586)

The three places that put a simulation's progress on the batch's live queue: a scenario's
frame per tick interval, a scenario's status, and the batch's status broadcast. A full queue is
the ordinary case and is skipped without a word. Before #586 every other failure was swallowed
too — a frame that could not be built, a queue that could not be reached — and the progress
screen simply stopped moving. Now a frame that cannot be built propagates, and a queue that
fails otherwise is reported and never raised: the screen is the only reader, and no scenario
depends on it.
"""

import queue
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import List, Optional

import pytest

import python.framework.batch.live_stats_coordinator as live_stats_coordinator
import python.framework.process.process_live_export as process_live_export
import python.framework.process.process_live_queue_helper as process_live_queue_helper
from python.framework.batch.live_stats_coordinator import LiveStatsCoordinator
from python.framework.types.live_types.live_stats_config_types import (
    ProcessLiveSetup,
    ScenarioStatus,
)
from python.framework.types.scenario_types.scenario_set_types import SingleScenario

_START = datetime(2026, 10, 1, tzinfo=timezone.utc)


class _Queue:
    """A live queue that takes frames, or fails the way it is told to."""

    def __init__(self, error: Optional[Exception] = None):
        self.error = error
        self.frames: list = []

    def put_nowait(self, frame) -> None:
        if self.error is not None:
            raise self.error
        self.frames.append(frame)


def _reports(monkeypatch, module) -> List[str]:
    """
    Capture what a module reports to the global log.

    Args:
        monkeypatch: pytest's monkeypatch
        module: The sending module

    Returns:
        The list the reports are appended to
    """
    reports: List[str] = []
    monkeypatch.setattr(module, 'vLog', SimpleNamespace(error=reports.append))
    return reports


def _status(live_queue: _Queue) -> None:
    """
    Send one scenario status the way a scenario process does.

    Args:
        live_queue: The queue
    """
    process_live_queue_helper.broadcast_status_update(
        live_queue=live_queue, scenario_index=0, scenario_name='scenario_a',
        live_stats_config=SimpleNamespace(enabled=True), status=ScenarioStatus.RUNNING)


class TestAScenarioStatus:

    def test_a_full_queue_is_skipped_without_a_word(self, monkeypatch):
        reports = _reports(monkeypatch, process_live_queue_helper)

        _status(_Queue(queue.Full()))

        assert reports == []

    def test_a_queue_that_fails_otherwise_is_reported_not_raised(self, monkeypatch):
        reports = _reports(monkeypatch, process_live_queue_helper)

        _status(_Queue(BrokenPipeError('the batch manager is gone')))

        assert len(reports) == 1
        assert reports[0].startswith('❌ [DISPLAY] A scenario status frame was not sent — '
                                     'BrokenPipeError: the batch manager is gone.')

    def test_a_frame_that_cannot_be_built_is_a_defect_and_propagates(self, monkeypatch):
        def unbuildable(**fields):
            raise TypeError('unexpected field')

        monkeypatch.setattr(process_live_queue_helper, 'LiveStatusFrame', unbuildable)

        with pytest.raises(TypeError):
            _status(_Queue())


class _FailsOnce(_Queue):
    """A live queue whose first put fails and every later one arrives."""

    def put_nowait(self, frame) -> None:
        if self.error is not None:
            error, self.error = self.error, None
            raise error
        self.frames.append(frame)


def _broadcast(live_queue: _Queue) -> None:
    """
    Broadcast one status to three scenarios the way the batch does.

    Args:
        live_queue: The queue
    """
    scenarios = [SingleScenario(name=f'scenario_{n}', scenario_index=n, symbol='BTCUSD',
                                data_broker_type='kraken_spot', start_date=_START)
                 for n in range(3)]
    LiveStatsCoordinator(scenarios, live_queue, enabled=True).broadcast_status(
        ScenarioStatus.WARMUP_DATA_TICKS)


class TestTheBatchBroadcast:

    def test_a_failing_queue_is_reported_once_per_broadcast(self, monkeypatch):
        reports = _reports(monkeypatch, live_stats_coordinator)

        _broadcast(_Queue(EOFError('manager closed')))

        assert len(reports) == 1
        assert reports[0].startswith('❌ [DISPLAY] A status broadcast was not sent — '
                                     'EOFError: manager closed.')

    def test_one_failure_does_not_cost_the_other_scenarios_their_status(self, monkeypatch):
        reports = _reports(monkeypatch, live_stats_coordinator)
        live_queue = _FailsOnce(BrokenPipeError('reset'))

        _broadcast(live_queue)

        assert len(reports) == 1
        assert [f.scenario_index for f in live_queue.frames] == [1, 2]
        assert all(f.status is ScenarioStatus.WARMUP_DATA_TICKS for f in live_queue.frames)


def _export(live_setup: ProcessLiveSetup) -> bool:
    """
    Run one tick of a scenario's live export, with stand-ins for what it reads.

    Args:
        live_setup: The scenario's live setup

    Returns:
        Whether the export sent (or tried to send) a frame
    """
    tick = SimpleNamespace(timestamp=_START + timedelta(seconds=5))
    portfolio = SimpleNamespace(balance=10010.0, initial_balance=10000.0,
                                get_total_trades=lambda: 1, get_winning_trades=lambda: 1,
                                get_losing_trades=lambda: 0, _positions_dirty=False)
    config = SimpleNamespace(symbol='BTCUSD', name='scenario_a', scenario_index=0,
                             live_stats_config=SimpleNamespace(export_portfolio_stats=False,
                                                               export_current_bars=False))
    workers = SimpleNamespace(decision_logic=SimpleNamespace(get_last_awareness=lambda: None))
    return process_live_export.process_live_export(
        live_setup=live_setup, config=config, tick_idx=4, tick=tick, portfolio=portfolio,
        worker_coordinator=workers, current_bars={})


def _live_setup(live_queue: _Queue) -> ProcessLiveSetup:
    """
    A scenario's live setup whose next tick is due for a frame.

    Args:
        live_queue: The queue

    Returns:
        The setup
    """
    return ProcessLiveSetup(live_queue=live_queue, last_update_time=0.0, update_interval_ms=0,
                            first_tick=SimpleNamespace(timestamp=_START), live_enabled=True,
                            tick_count=10)


class TestAScenarioFrame:

    def test_a_frame_reaches_the_queue(self, monkeypatch):
        reports = _reports(monkeypatch, process_live_export)
        live_queue = _Queue()

        assert _export(_live_setup(live_queue)) is True

        assert len(live_queue.frames) == 1
        assert live_queue.frames[0].progress_percent == 50.0
        assert reports == []

    def test_a_full_queue_skips_the_frame_and_tries_again_next_time(self, monkeypatch):
        reports = _reports(monkeypatch, process_live_export)
        live_setup = _live_setup(_Queue(queue.Full()))

        _export(live_setup)

        assert reports == []
        assert live_setup.live_enabled is True
        assert live_setup.last_update_time == 0.0

    def test_a_queue_that_fails_otherwise_is_reported_once_and_the_frames_stop(
            self, monkeypatch):
        reports = _reports(monkeypatch, process_live_export)
        live_queue = _Queue(BrokenPipeError('the batch manager is gone'))
        live_setup = _live_setup(live_queue)

        _export(live_setup)
        sent_again = _export(live_setup)

        assert len(reports) == 1
        assert reports[0].startswith('❌ [DISPLAY] A live progress frame of scenario_a was not '
                                     'sent — BrokenPipeError: the batch manager is gone.')
        assert live_setup.live_enabled is False
        assert sent_again is False
