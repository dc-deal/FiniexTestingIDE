"""
What a Worker Receives of the Market — read-only ticks and bars (#654).

The engine hands one tick, the forming bars and the bar history to every worker and to the decision,
and each of them reads the same objects. So a worker receives the tick and the forming bars as masks
and the history as tuples of frozen bars: what one of them reads, none of them can change for the
next.
"""

import copy
import dataclasses
import pickle
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from typing import Dict, List
from unittest.mock import MagicMock

import pytest

from python.framework.bars.bar_rendering_controller import BarRenderingController
from python.framework.factory.decision_logic_factory import DecisionLogicFactory
from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.trading_env.algo_masks.algo_bar import AlgoBar
from python.framework.trading_env.algo_masks.algo_order import AlgoOrder
from python.framework.trading_env.algo_masks.algo_tick import AlgoTick
from python.framework.types.decision_logic_types import Decision, DecisionLogicAction
from python.framework.types.market_types.market_data_types import Bar, FormingBar, TickData
from python.framework.types.trading_env_types.latency_simulator_types import PendingOrder
from python.framework.types.worker_types import ComputeBasis, WorkerResult
from python.framework.workers.worker_orchestrator import WorkerOrchestrator
from tests.shared.null_logger import NullLogger

_T0 = datetime(2026, 11, 25, 9, tzinfo=timezone.utc)
_SYMBOL = 'ETHUSD'


def _tick(seconds: float, bid: float = 2000.0) -> TickData:
    stamp = _T0 + timedelta(seconds=seconds)
    msc = int(stamp.timestamp() * 1000)
    return TickData(timestamp=stamp, symbol=_SYMBOL, bid=bid, ask=bid + 0.5, volume=0.1,
                    time_msc=msc, collected_msc=msc + 40, is_clipped=False)


def _closed_bar(minute: int, close: float) -> Bar:
    opened = _T0 - timedelta(minutes=minute)
    return Bar(symbol=_SYMBOL, timeframe='M1', timestamp=opened.isoformat(), open=close,
               high=close + 1.0, low=close - 1.0, close=close, volume=3.0, tick_count=12)


def _controller() -> BarRenderingController:
    controller = BarRenderingController(NullLogger())
    controller.register_workers([SimpleNamespace(get_required_timeframes=lambda: ['M1'])])
    return controller


class TestTheTickMask:

    def test_it_shows_the_market_fields(self):
        tick = _tick(0)
        mask = AlgoTick(tick)
        assert AlgoTick.get_mask_fields() == (
            'timestamp', 'symbol', 'bid', 'ask', 'mid', 'price', 'last', 'volume')
        for name in AlgoTick.get_mask_fields():
            assert getattr(mask, name) == getattr(tick, name)

    def test_the_engines_own_fields_are_not_shown(self):
        mask = AlgoTick(_tick(0))
        for name in ('time_msc', 'collected_msc', 'is_clipped'):
            with pytest.raises(AttributeError):
                getattr(mask, name)

    def test_assigning_changes_nothing(self):
        tick = _tick(0)
        with pytest.raises(AttributeError, match='read-only'):
            AlgoTick(tick).bid = 1.0
        assert tick.bid == 2000.0

    def test_a_logic_makes_one_mask_per_tick(self, tmp_path):
        logger = ScenarioLogger('algo_market_data', 'algo_market_data', _T0,
                                'run_algo_market_data', log_root_override=tmp_path)
        logic = DecisionLogicFactory(logger).create_logic(
            logic_type='CORE/simple_consensus', logger=logger, logic_config={})
        first, second = _tick(0), _tick(1)
        assert logic.get_algo_tick(first) is logic.get_algo_tick(first)
        assert logic.get_algo_tick(first).is_mask_of(first)
        assert logic.get_algo_tick(second).is_mask_of(second)


class TestTheBars:

    def test_a_closed_bar_cannot_be_changed(self):
        bar = _closed_bar(1, 2000.0)
        with pytest.raises(dataclasses.FrozenInstanceError):
            bar.close = 1.0

    def test_a_forming_bar_has_the_fields_of_a_closed_one(self):
        assert ([(field.name, field.type) for field in dataclasses.fields(FormingBar)]
                == [(field.name, field.type) for field in dataclasses.fields(Bar)])

    def test_the_history_holds_a_frozen_copy_of_each_closed_bar(self):
        controller = _controller()
        forming = controller.process_tick(_tick(0, 2000.0))['M1']
        controller.process_tick(_tick(30, 2002.0))
        controller.process_tick(_tick(61, 2005.0))      # the next minute closes the first bar
        history = controller.get_bar_history(_SYMBOL, 'M1')
        assert isinstance(history, tuple) and len(history) == 1
        closed = history[0]
        assert type(closed) is Bar and closed is not forming
        assert (closed.open, closed.high, closed.low, closed.close, closed.tick_count) == (
            2000.25, 2002.25, 2000.25, 2002.25, 2)
        assert closed.is_complete
        forming.update_with_tick(9999.0)               # the renderer's old bar is no reader's
        assert controller.get_bar_history(_SYMBOL, 'M1')[0].close == 2002.25

    def test_the_history_cannot_be_appended_to(self):
        controller = _controller()
        controller.inject_warmup_bars(_SYMBOL, {'M1': [_closed_bar(2, 1990.0)]}, _T0)
        history = controller.get_all_bar_history(_SYMBOL)['M1']
        with pytest.raises(AttributeError):
            history.append(_closed_bar(1, 1995.0))

    def test_warmup_bars_enter_complete_and_the_handed_in_ones_stay(self):
        handed_in = [_closed_bar(3, 1990.0), _closed_bar(2, 1992.0)]
        controller = _controller()
        controller.inject_warmup_bars(_SYMBOL, {'M1': handed_in}, _T0)
        assert all(bar.is_complete for bar in controller.get_bar_history(_SYMBOL, 'M1'))
        assert not any(bar.is_complete for bar in handed_in)

    def test_the_forming_bar_mask_reads_the_bar_as_it_moves(self):
        bar = FormingBar(symbol=_SYMBOL, timeframe='M1', timestamp=_T0.isoformat(), open=0,
                         high=0, low=0, close=0, volume=0)
        mask = AlgoBar(bar)
        bar.update_with_tick(2000.0, 0.1)
        bar.update_with_tick(2003.0, 0.2)
        assert (mask.open, mask.high, mask.low, mask.close, mask.tick_count) == (
            2000.0, 2003.0, 2000.0, 2003.0, 2)
        assert mask.ohlc == bar.ohlc and mask.to_dict() == bar.to_dict()
        with pytest.raises(AttributeError, match='read-only'):
            mask.close = 1.0
        assert bar.close == 2003.0


class TestCopies:
    """A mask is no snapshot unless its copy is well defined, and then it says so."""

    def test_a_tick_mask_is_its_own_copy(self):
        mask = AlgoTick(_tick(0))
        assert copy.copy(mask) is mask and copy.deepcopy(mask) is mask

    def test_a_forming_bar_mask_copies_as_the_bar_as_it_stands(self):
        bar = FormingBar(symbol=_SYMBOL, timeframe='M1', timestamp=_T0.isoformat(), open=0,
                         high=0, low=0, close=0, volume=0)
        bar.update_with_tick(2000.0, 0.1)
        snapshot = copy.copy(AlgoBar(bar))
        bar.update_with_tick(2010.0, 0.1)
        assert type(snapshot) is Bar and snapshot.close == 2000.0
        assert copy.deepcopy(AlgoBar(bar)).close == 2010.0

    def test_any_other_mask_refuses_with_a_reason(self):
        mask = AlgoOrder(PendingOrder(pending_order_id='L1'))
        for attempt in (copy.copy, copy.deepcopy, pickle.dumps):
            with pytest.raises(TypeError, match='cannot be copied or pickled'):
                attempt(mask)


class _RecordingWorker:
    """A LIVE worker on M1 that keeps what it receives."""

    def __init__(self):
        self.name = 'recorder'
        self.performance_logger = None
        self.received: List[tuple] = []

    def get_compute_basis(self) -> ComputeBasis:
        return ComputeBasis.LIVE

    def get_required_timeframes(self) -> List[str]:
        return ['M1']

    def should_recompute(self, tick, bar_updated: bool) -> bool:
        return True

    def set_state(self, state) -> None:
        pass

    def compute(self, tick, bar_history, current_bars) -> WorkerResult:
        self.received.append((tick, bar_history, current_bars))
        return WorkerResult(outputs={})


class _RecordingDecisionLogic:
    """The decision surface process_tick uses, keeping the tick compute_tick receives."""

    def __init__(self):
        self.performance_logger = None
        self.ticks: List[AlgoTick] = []
        self._masks: Dict[int, AlgoTick] = {}

    def get_algo_tick(self, tick: TickData) -> AlgoTick:
        return self._masks.setdefault(id(tick), AlgoTick(tick))

    def compute_tick(self, tick, worker_results) -> Decision:
        self.ticks.append(tick)
        return Decision(action=DecisionLogicAction.FLAT, outputs={})


def _orchestrator(worker: _RecordingWorker, logic: _RecordingDecisionLogic) -> WorkerOrchestrator:
    orch = object.__new__(WorkerOrchestrator)
    orch.is_initialized = True
    orch.workers = {worker.name: worker}
    orch._worker_results = {}
    orch._algo_bars = {}
    orch._signal_workers = {}
    orch._signal_stale_state = {}
    orch.logger = MagicMock()
    orch.parallel_workers = False
    orch._coordination_stats = SimpleNamespace(ticks_processed=0)
    orch.decision_logic = logic
    orch.tick_logger = SimpleNamespace(log_tick_data=lambda **kw: None)
    return orch


class TestWhatTheWorkersReceive:

    def _run(self, seconds: List[float]):
        controller = _controller()
        worker, logic = _RecordingWorker(), _RecordingDecisionLogic()
        orch = _orchestrator(worker, logic)
        ticks = []
        for second in seconds:
            tick = _tick(second, 2000.0 + second)
            ticks.append(tick)
            orch.process_tick(tick, controller.process_tick(tick),
                              controller.get_all_bar_history(_SYMBOL))
        return controller, worker, logic, ticks

    def test_the_tick_arrives_as_its_mask_for_workers_and_decision(self):
        _, worker, logic, ticks = self._run([0])
        received_tick = worker.received[0][0]
        assert isinstance(received_tick, AlgoTick) and received_tick.is_mask_of(ticks[0])
        assert logic.ticks[0] is received_tick

    def test_the_forming_bar_arrives_as_one_mask_until_its_period_closes(self):
        controller, worker, _, _ = self._run([0, 20, 61])
        masks = [current_bars['M1'] for _, _, current_bars in worker.received]
        assert all(isinstance(mask, AlgoBar) for mask in masks)
        assert masks[0] is masks[1]                    # the same period
        assert masks[2] is not masks[1]                # the next period's bar
        assert masks[2].is_mask_of(controller.get_current_bar(_SYMBOL, 'M1'))

    def test_no_worker_can_change_the_bars_another_receives(self):
        _, worker, _, _ = self._run([0, 61])
        _, bar_history, current_bars = worker.received[-1]
        with pytest.raises(TypeError):
            current_bars['M1'] = None
        assert isinstance(bar_history['M1'], tuple)
        assert all(type(bar) is Bar for bar in bar_history['M1'])
