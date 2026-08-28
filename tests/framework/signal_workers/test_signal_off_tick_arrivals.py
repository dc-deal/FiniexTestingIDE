"""
Off-tick signal arrivals (#141 Part 2a): an envelope that lands BETWEEN two ticks.

The heartbeat path forwards cached worker results by design, so without this seam a pushed
envelope would wait for the next tick — minutes on a quiet instrument, which is exactly what
a push channel exists to avoid. These tests pin the seam and, just as importantly, pin what it
must NOT disturb: the three resolution counters stay tick-weighted, because the ledger's
signal_fresh_ratio is defined on that base.
"""

from types import SimpleNamespace
from typing import Any, Dict, List

import pytest
from tests.framework.signal_workers.conftest import SYMBOL, make_provider, make_tick, snapshot, utc

from python.framework.decision_logic.abstract_decision_logic import AbstractDecisionLogic
from python.framework.signal_data.transport.signal_inbox import SignalInbox
from python.framework.types.decision_logic_types import Decision, DecisionLogicAction
from python.framework.types.trading_env_types.order_types import OrderType
from python.framework.types.worker_types import WorkerRequirement
from python.framework.workers.core.llm_sentiment_worker import LlmSentimentWorker
from python.framework.workers.worker_orchestrator import WorkerOrchestrator

SIGNAL_KIND = 'llm_sentiment'


class _StaleRecordingDecision(AbstractDecisionLogic):
    """Contract-complete decision that records the stale edges it is told about."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.stale_edges: List[str] = []

    @classmethod
    def get_required_order_types(cls, decision_logic_config: Dict[str, Any]) -> List[OrderType]:
        return [OrderType.MARKET]

    def get_required_workers(self) -> Dict[str, WorkerRequirement]:
        return {'sentiment': WorkerRequirement.of('CORE/llm_sentiment', 'sentiment_score')}

    def compute_tick(self, tick, worker_results):
        return Decision(action=DecisionLogicAction.FLAT, outputs={})

    def _execute_decision_impl(self, decision, tick):
        return []

    def on_market_data_stale(self, status):
        pass

    def on_signal_stale(self, worker_name: str, signal_kind: str) -> None:
        self.stale_edges.append(worker_name)


def _orchestrator(mock_logger, provider):
    """An orchestrator around one SIGNAL worker with the provider already injected."""
    worker = LlmSentimentWorker(
        name='sentiment',
        parameters={'max_staleness_minutes': 30, 'signal_delay_minutes': 0},
        logger=mock_logger,
        trading_context=SimpleNamespace(symbol=SYMBOL),
    )
    worker.set_signal_provider(provider)
    orchestrator = WorkerOrchestrator(
        decision_logic=_StaleRecordingDecision('stub', mock_logger, {}),
        strategy_config={'worker_instances': {'sentiment': 'CORE/llm_sentiment'}},
        workers=[worker],
        parallel_workers=False,
    )
    orchestrator.is_initialized = True
    orchestrator.tick_logger = SimpleNamespace(log_tick_data=lambda **kw: None)
    return orchestrator


def _stats(orchestrator):
    """The single SIGNAL worker's counters."""
    rows = orchestrator.get_signal_statistics()
    assert len(rows) == 1
    return rows[0]


@pytest.fixture
def provider():
    """One snapshot at 08:00 — later arrivals are merged in by the tests."""
    return make_provider(snapshot(utc(2026, 1, 15, 8, 0), 0.1, 0.5, signal='HOLD'))


class TestMergeOnly:
    """The tick path merges and lets the existing worker pass do the rest."""

    def test_merge_reports_what_was_new(self, mock_logger, provider):
        orchestrator = _orchestrator(mock_logger, provider)
        arrival = snapshot(utc(2026, 1, 15, 8, 10), 0.3, 0.8, signal='BUY')
        assert orchestrator.merge_signal_arrivals({SIGNAL_KIND: [arrival]}) == 1

    def test_merge_touches_no_counter(self, mock_logger, provider):
        orchestrator = _orchestrator(mock_logger, provider)
        orchestrator.merge_signal_arrivals(
            {SIGNAL_KIND: [snapshot(utc(2026, 1, 15, 8, 10), 0.3, 0.8)]})
        stats = _stats(orchestrator)
        assert (stats.fresh_ticks, stats.stale_ticks,
                stats.blind_ticks, stats.off_tick_arrivals) == (0, 0, 0, 0)

    def test_merged_snapshot_is_visible_to_the_next_tick(self, mock_logger, provider):
        orchestrator = _orchestrator(mock_logger, provider)
        orchestrator.merge_signal_arrivals(
            {SIGNAL_KIND: [snapshot(utc(2026, 1, 15, 8, 10), 0.3, 0.8, signal='BUY')]})
        orchestrator.process_tick(make_tick(utc(2026, 1, 15, 8, 11)), current_bars={})
        assert orchestrator.get_worker_result('sentiment').outputs['signal'] == 'BUY'

    def test_unknown_source_is_ignored(self, mock_logger, provider):
        orchestrator = _orchestrator(mock_logger, provider)
        assert orchestrator.merge_signal_arrivals(
            {'some_other_source': [snapshot(utc(2026, 1, 15, 8, 10), 0.3, 0.8)]}) == 0


class TestOffTickRefresh:
    """The heartbeat path refreshes, because nothing else would."""

    def test_arrival_reaches_the_worker_without_a_tick(self, mock_logger, provider):
        orchestrator = _orchestrator(mock_logger, provider)
        orchestrator.process_tick(make_tick(utc(2026, 1, 15, 8, 1)), current_bars={})
        assert orchestrator.get_worker_result('sentiment').outputs['signal'] == 'HOLD'

        orchestrator.process_signal_arrivals(
            {SIGNAL_KIND: [snapshot(utc(2026, 1, 15, 8, 5), 0.9, 0.9, signal='BUY')]},
            utc(2026, 1, 15, 8, 5))
        assert orchestrator.get_worker_result('sentiment').outputs['signal'] == 'BUY'

    def test_an_off_tick_compute_is_recorded_as_a_compute(self, mock_logger, provider):
        """
        Found on the first live observation run: a worker refreshed three times, all of them
        off-tick, and the run report said `0 computes` while the log showed the arrivals.

        The tick path times and records every compute; this path did not, so a worker whose
        first arrival lands BEFORE the first tick — which is the normal case, the transport
        starts before the market does — is invisible in the performance section for the rest
        of the session. A number an operator reads must not contradict the log beside it.
        """
        orchestrator = _orchestrator(mock_logger, provider)
        worker = orchestrator.workers['sentiment']
        before = worker.performance_logger.get_stats().worker_call_count

        orchestrator.process_signal_arrivals(
            {SIGNAL_KIND: [snapshot(utc(2026, 1, 15, 8, 5), 0.9, 0.9, signal='BUY')]},
            utc(2026, 1, 15, 8, 5))

        assert worker.performance_logger.get_stats().worker_call_count == before + 1

    def test_a_skipped_refresh_records_nothing(self, mock_logger, provider):
        """An arrival that does not move the window is not a compute."""
        orchestrator = _orchestrator(mock_logger, provider)
        snap = snapshot(utc(2026, 1, 15, 8, 5), 0.9, 0.9, signal='BUY')
        orchestrator.process_signal_arrivals({SIGNAL_KIND: [snap]}, utc(2026, 1, 15, 8, 5))
        worker = orchestrator.workers['sentiment']
        after_first = worker.performance_logger.get_stats().worker_call_count

        orchestrator.process_signal_arrivals({SIGNAL_KIND: [snap]}, utc(2026, 1, 15, 8, 6))
        assert worker.performance_logger.get_stats().worker_call_count == after_first

    def test_off_tick_arrivals_are_counted_separately(self, mock_logger, provider):
        orchestrator = _orchestrator(mock_logger, provider)
        orchestrator.process_tick(make_tick(utc(2026, 1, 15, 8, 1)), current_bars={})
        orchestrator.process_signal_arrivals(
            {SIGNAL_KIND: [snapshot(utc(2026, 1, 15, 8, 5), 0.9, 0.9, signal='BUY')]},
            utc(2026, 1, 15, 8, 5))

        stats = _stats(orchestrator)
        assert stats.off_tick_arrivals == 1
        # The tick invariant: one tick ran, so exactly one resolution was counted.
        assert stats.fresh_ticks + stats.stale_ticks + stats.blind_ticks == 1

    def test_redelivery_changes_nothing(self, mock_logger, provider):
        """The producer is at-least-once; a repeat must not refresh or count."""
        orchestrator = _orchestrator(mock_logger, provider)
        arrival = snapshot(utc(2026, 1, 15, 8, 5), 0.9, 0.9, signal='BUY')
        orchestrator.process_signal_arrivals({SIGNAL_KIND: [arrival]}, utc(2026, 1, 15, 8, 5))
        merged = orchestrator.process_signal_arrivals(
            {SIGNAL_KIND: [arrival]}, utc(2026, 1, 15, 8, 6))

        assert merged == 0
        assert _stats(orchestrator).off_tick_arrivals == 1

    def test_empty_drain_is_a_no_op(self, mock_logger, provider):
        """The simulation and a mock session are exactly this case, every pass."""
        orchestrator = _orchestrator(mock_logger, provider)
        assert orchestrator.process_signal_arrivals({}, utc(2026, 1, 15, 8, 5)) == 0
        assert _stats(orchestrator).off_tick_arrivals == 0

    def test_arrival_ending_an_outage_recovers_off_tick(self, mock_logger, provider):
        """A feed that died is revived by the arrival itself, not by the next tick."""
        orchestrator = _orchestrator(mock_logger, provider)
        # An hour past the 30-minute staleness threshold → stale, edge fires.
        orchestrator.process_tick(make_tick(utc(2026, 1, 15, 9, 0)), current_bars={})
        assert orchestrator.get_worker_result('sentiment').is_stale is True

        orchestrator.process_signal_arrivals(
            {SIGNAL_KIND: [snapshot(utc(2026, 1, 15, 9, 0, 30), 0.4, 0.7)]},
            utc(2026, 1, 15, 9, 1))
        assert orchestrator.get_worker_result('sentiment').is_stale is False


class TestInbox:
    """The hand-off itself."""

    def test_drain_empties_and_groups_by_source(self):
        inbox = SignalInbox()
        first = snapshot(utc(2026, 1, 15, 8, 10), 0.3, 0.8)
        second = snapshot(utc(2026, 1, 15, 8, 20), 0.4, 0.8)
        inbox.put(SIGNAL_KIND, [first])
        inbox.put(SIGNAL_KIND, [second])

        assert inbox.get_pending_count() == 2
        assert inbox.drain() == {SIGNAL_KIND: [first, second]}
        assert inbox.get_pending_count() == 0
        assert inbox.drain() == {}

    def test_total_received_survives_the_drain(self):
        inbox = SignalInbox()
        inbox.put(SIGNAL_KIND, [snapshot(utc(2026, 1, 15, 8, 10), 0.3, 0.8)])
        inbox.drain()
        assert inbox.get_total_received() == 1

    def test_empty_put_is_ignored(self):
        inbox = SignalInbox()
        inbox.put(SIGNAL_KIND, [])
        assert inbox.drain() == {}
