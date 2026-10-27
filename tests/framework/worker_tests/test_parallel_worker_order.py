"""
Parallel workers hand the decision logic their results in declaration order, as sequential workers do.

The orchestrator's results dict keeps the order its keys first arrived in. The parallel path used to
fill it as threads finished, so the order hung on thread timing, and a logic that iterates its
results could decide differently from one run to the next — on a path shared with live trading. The
composite key counts the parallelisation settings as neutral only while parallel and sequential
execution give the same result (#615), and these tests are that condition: one with workers built to
finish in reverse, one over a whole backtest run both ways.
"""

import time
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from typing import Any, Dict, List
from unittest.mock import MagicMock

from python.configuration.app_config_manager import AppConfigManager
from python.framework.batch.batch_orchestrator import BatchOrchestrator
from python.framework.types.api.report_types import RunReporting
from python.framework.workers.worker_orchestrator import WorkerOrchestrator
from python.scenario.scenario_config_loader import ScenarioConfigLoader
from python.scenario.scenario_set import ScenarioSet
from tests.shared.fixture_helpers import extract_portfolio_stats, extract_trade_history
from tests.shared.parity_comparators import assert_portfolio_equal, assert_trades_equal

# Two workers, two scenarios — the parallel path needs more than one worker to run at all.
_SET = 'configs/scenario_sets/backtesting/trend_channel_reference_sweep_base.json'
_TICKS_PER_SCENARIO = 60000


class _SlowWorker:
    """A worker that answers after a delay of its own, so finishing order can be forced."""

    def __init__(self, name: str, delay_s: float):
        self.name = name
        self.delay_s = delay_s
        self.performance_logger = None

    def set_state(self, state: Any) -> None:
        pass

    def compute(self) -> str:
        time.sleep(self.delay_s)
        return f'{self.name}-result'


def _orchestrator(workers: List[_SlowWorker], parallel: bool) -> WorkerOrchestrator:
    """
    An orchestrator reduced to its worker pass, around workers that need no bars.

    Args:
        workers: The workers, in declaration order
        parallel: Whether the parallel path's thread pool exists

    Returns:
        The orchestrator
    """
    orchestrator = object.__new__(WorkerOrchestrator)
    orchestrator.workers = {worker.name: worker for worker in workers}
    orchestrator._worker_results = {}
    orchestrator._thread_pool = ThreadPoolExecutor(max_workers=len(workers)) if parallel else None
    orchestrator._coordination_stats = SimpleNamespace(parallel_time_saved_ms=0.0, ticks_processed=0)
    orchestrator.logger = MagicMock()
    orchestrator._worker_needs_recompute = lambda *args: True
    orchestrator._filter_bar_history_for_worker = lambda worker, bar_history: bar_history
    orchestrator._run_worker = lambda worker, tick, bar_history, current_bars: worker.compute()
    return orchestrator


class TestTheResultOrder:

    def test_parallel_results_arrive_in_declaration_order(self):
        # The first worker is the slowest, so in completion order it would come last.
        workers = [_SlowWorker('first', 0.15), _SlowWorker('second', 0.05), _SlowWorker('third', 0.0)]
        orchestrator = _orchestrator(workers, parallel=True)
        orchestrator._process_workers_parallel(None, False, {}, {}, set())
        orchestrator._thread_pool.shutdown(wait=True)
        assert list(orchestrator._worker_results) == ['first', 'second', 'third']

    def test_parallel_and_sequential_hand_over_the_same_results(self):
        parallel = _orchestrator(
            [_SlowWorker('first', 0.1), _SlowWorker('second', 0.0)], parallel=True)
        sequential = _orchestrator(
            [_SlowWorker('first', 0.1), _SlowWorker('second', 0.0)], parallel=False)
        parallel._process_workers_parallel(None, False, {}, {}, set())
        parallel._thread_pool.shutdown(wait=True)
        sequential._process_workers_sequential(None, False, {}, {}, set())
        assert list(parallel._worker_results.items()) == list(sequential._worker_results.items())


def _run(parallel_workers: bool) -> Dict[str, Any]:
    """
    Run the set once, every scenario with the given parallelisation.

    Args:
        parallel_workers: Whether its workers run in parallel

    Returns:
        Scenario name → (trade history, portfolio stats)
    """
    loaded = ScenarioConfigLoader().load_config(_SET)
    for scenario in loaded.scenarios:
        scenario.execution_config = {**(scenario.execution_config or {}),
                                     'parallel_workers': parallel_workers}
        # A slice of each window keeps the suite fast; it still closes trades to compare.
        scenario.max_ticks = _TICKS_PER_SCENARIO
    app_config = AppConfigManager()
    scenario_set = ScenarioSet(loaded, app_config, reporting=RunReporting.NONE)
    try:
        summary = BatchOrchestrator(scenario_set, app_config).run()
    finally:
        scenario_set.close_loggers()
    names = [scenario.name for scenario in loaded.scenarios]
    for result in summary.process_result_list:
        # The comparison proves something only if the parallel run took the parallel path.
        assert result.tick_loop_results.coordination_statistics.parallel_workers is parallel_workers
    return {name: (extract_trade_history(result.tick_loop_results),
                   extract_portfolio_stats(result.tick_loop_results))
            for name, result in zip(names, summary.process_result_list)}


class TestABacktestRunBothWays:

    def test_parallel_and_sequential_trade_alike(self):
        parallel = _run(parallel_workers=True)
        sequential = _run(parallel_workers=False)
        assert parallel.keys() == sequential.keys()
        assert any(trades for trades, _ in parallel.values()), 'the set closed no trade — nothing compared'
        for name in parallel:
            assert_trades_equal(parallel[name][0], sequential[name][0])
            assert_portfolio_equal(parallel[name][1], sequential[name][1])
