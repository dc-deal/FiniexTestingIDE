"""
Field Study — the session wiring and its preflight (#332, #566).

`open_field_study` opens the capture, hangs the projection on the executor's record and refuses
to trade beside a resting order; `close_field_study` writes what reconciliation did and the REST
telemetry before the end marker. Every refusal and every end leaves a terminated capture behind —
a header with no end is a run that cannot be told from one still going.
"""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict, List

import pytest

from python.framework.autotrader.field_study_setup import close_field_study, open_field_study
from python.framework.reporting.field_study_stream_projection import FieldStudyStreamProjection
from python.framework.types.config_types.market_config_types import TradingModel
from python.framework.types.live_types.api_perf_types import ApiEndpointStats, ApiPerfSnapshot
from python.framework.types.live_types.live_execution_types import BrokerOrderStatus
from python.framework.types.live_types.reconciliation_types import BrokerOrder, FlatCheckResult
from python.framework.types.trading_env_types.order_types import OrderDirection, OrderType
from tests.autotrader.cold_start.conftest import RecordingLogger


class _Executor:
    """The two listener registrations the setup makes, kept for inspection."""

    def __init__(self):
        self.order_listeners: List[Any] = []
        self.truth_listeners: List[Any] = []

    def add_order_event_listener(self, listener) -> None:
        self.order_listeners.append(listener)

    def add_broker_truth_listener(self, listener) -> None:
        self.truth_listeners.append(listener)


def _study() -> SimpleNamespace:
    return SimpleNamespace(get_phase_ids=lambda: ['p1'], set_recorder=lambda recorder: None)


def _reconciler(open_orders=(), raises=None, counters: Dict[str, int] = None) -> SimpleNamespace:
    def is_account_flat():
        if raises is not None:
            raise raises
        return FlatCheckResult(is_flat=not open_orders, open_orders=list(open_orders))
    return SimpleNamespace(
        is_account_flat=is_account_flat,
        get_display_counters=lambda: counters or {'reconcile_count': 4, 'reconcile_skipped': 1,
                                                  'reconcile_total_divergences': 2})


def _open(tmp_path: Path, reconciler, trading_model=TradingModel.SPOT):
    executor = _Executor()
    setup = open_field_study(
        decision_logic=_study(), executor=executor, reconciler=reconciler,
        trading_model=trading_model, run_dir=tmp_path, unit_name='fs', symbol='ETHUSD',
        logger=RecordingLogger())
    return setup, executor


def _lines(tmp_path: Path) -> List[Dict[str, Any]]:
    """The capture's lines after its header."""
    text = (tmp_path / 'field_study.jsonl').read_text(encoding='utf-8').splitlines()
    return [json.loads(line) for line in text[1:] if line.strip()]


class TestTheRefusals:

    def test_an_account_model_it_cannot_run_opens_no_capture(self, tmp_path):
        setup, executor = _open(tmp_path, _reconciler(), TradingModel.MARGIN)

        assert (setup.proceed, setup.recorder) == (False, None)
        assert not (tmp_path / 'field_study.jsonl').exists()
        assert executor.order_listeners == []

    def test_a_resting_order_stops_the_run_before_it_trades(self, tmp_path):
        resting = BrokerOrder(broker_ref='OABC', symbol='ETHUSD', direction=OrderDirection.LONG,
                              order_type=OrderType.LIMIT, lots=0.002,
                              status=BrokerOrderStatus.PENDING)
        setup, _ = _open(tmp_path, _reconciler(open_orders=[resting]))

        assert setup.proceed is False
        assert setup.recorder is not None, 'the session closes it on its way out'

    def test_no_reconciler_trades_with_a_warning(self, tmp_path):
        setup, executor = _open(tmp_path, None)

        assert setup.proceed is True
        assert isinstance(executor.order_listeners[0], FieldStudyStreamProjection)
        assert len(executor.truth_listeners) == 1

    def test_a_preflight_that_raises_leaves_a_terminated_capture(self, tmp_path):
        with pytest.raises(ConnectionError):
            _open(tmp_path, _reconciler(raises=ConnectionError('venue unreachable')))

        assert _lines(tmp_path)[-1]['event_type'] == 'session_end'


class TestTheEnd:

    def test_the_totals_come_before_the_end_marker(self, tmp_path):
        setup, _ = _open(tmp_path, None)
        monitor = SimpleNamespace(get_snapshot=lambda: ApiPerfSnapshot(
            endpoints=[ApiEndpointStats(endpoint='/0/private/AddOrder', count=3, min_ms=90.0,
                                        max_ms=150.0, total_ms=360.0)],
            slow_count=1, total_errors=0))

        close_field_study(setup.recorder, monitor, _reconciler())

        tail = [line['event_type'] for line in _lines(tmp_path)][-3:]
        assert tail == ['reconcile_summary', 'api_perf', 'session_end']
        lines = _lines(tmp_path)
        assert lines[-3]['reconcile'] == {'enabled': True, 'cycles': 4, 'skipped': 1,
                                          'divergences_seen': 2}
        endpoint = lines[-2]['api_perf']['endpoints'][0]
        assert (endpoint['calls'], endpoint['avg_ms']) == (3, pytest.approx(120.0))
        assert lines[-2]['api_perf']['slow_calls'] == 1

    def test_reconciliation_switched_off_says_so(self, tmp_path):
        setup, _ = _open(tmp_path, None)

        close_field_study(setup.recorder, None, None)

        summary = next(line for line in _lines(tmp_path)
                       if line['event_type'] == 'reconcile_summary')
        assert summary['reconcile'] == {'enabled': False}
