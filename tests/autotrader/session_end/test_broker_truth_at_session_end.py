"""
FiniexTestingIDE - Broker Truth at the Session's End (#362)

At its end a session asks the venue what it holds once the session's own orders are handled, and
writes the answer into the order-event stream before it closes. The read sits in its own guard: a
venue that will not answer — or refuses outright — never keeps the stream open. The field study's
recorder follows the same rule for its own end snapshot.
"""

from types import SimpleNamespace
from typing import List

from python.framework.autotrader.autotrader_main import AutotraderMain
from python.framework.exceptions.connection_errors import ConnectionInadmissibleError
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.types.autotrader_types.autotrader_result_types import AutoTraderResult
from python.framework.types.live_types.broker_truth_types import BrokerTruthReadReason
from tests.autotrader.cold_start.conftest import RecordingLogger


def _session(monkeypatch, calls: List[str], truth_fails: bool = False) -> AutotraderMain:
    """
    A session reduced to what its shutdown touches, recording the order of the steps.

    Args:
        monkeypatch: To set aside the reporting the shutdown ends in
        calls: Where each step writes its name
        truth_fails: The venue refuses the end read

    Returns:
        The session, ready to shut down
    """
    session = AutotraderMain(AutoTraderConfig(
        profile_name='my_bot_live', symbol='BTCUSD', broker_type='kraken_spot'))
    session._global_logger = RecordingLogger()
    session._session_logger = RecordingLogger()

    def record_session_truth(read_reason: BrokerTruthReadReason) -> None:
        calls.append(f'truth:{read_reason.value}')
        if truth_fails:
            raise ConnectionInadmissibleError('credentials refused')

    session._executor = SimpleNamespace(
        finish_remaining_orders=lambda cancel_orders: calls.append('orders handled'),
        check_clean_shutdown=lambda expect_flat: True,
        record_session_truth=record_session_truth)
    session._order_event_stream = SimpleNamespace(close=lambda: calls.append('stream closed'))
    monkeypatch.setattr(session, '_persist_cold_start_carry_over', lambda: True)
    monkeypatch.setattr(session, '_collect_results', lambda ticks, clipped: AutoTraderResult())
    monkeypatch.setattr(session, '_generate_reports', lambda result: None)
    return session


class TestTheEndRead:
    def test_it_comes_after_the_orders_and_before_the_stream_closes(self, monkeypatch):
        calls: List[str] = []

        _session(monkeypatch, calls)._shutdown(0, 0)

        assert calls == ['orders handled', 'truth:session_end', 'stream closed']

    def test_a_read_that_fails_never_keeps_the_stream_open(self, monkeypatch):
        calls: List[str] = []
        session = _session(monkeypatch, calls, truth_fails=True)

        session._shutdown(0, 0)

        assert calls[-1] == 'stream closed'
        assert any('Broker truth at session end not recorded' in line
                   for line in session._session_logger.errors)


class TestTheFieldStudysEnd:
    def test_its_recorder_closes_when_its_snapshot_fails(self, monkeypatch):
        calls: List[str] = []
        session = _session(monkeypatch, calls)
        session._field_study_recorder = SimpleNamespace(
            close=lambda reason: calls.append('field study closed'))

        def snapshot_fails(phase, flat):
            raise ConnectionInadmissibleError('credentials refused')
        monkeypatch.setattr(session, '_record_field_study_broker_truth', snapshot_fails)

        session._shutdown(0, 0)

        assert 'field study closed' in calls
        assert any('Field Study broker truth at session end not recorded' in line
                   for line in session._session_logger.errors)
