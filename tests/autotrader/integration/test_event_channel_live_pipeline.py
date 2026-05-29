"""
FiniexTestingIDE - Decision Event Channel — AutoTrader Pipeline (#348)

Runs the event-probe decision logic through the AutoTrader-mock pipeline and
asserts the ordered decision-event sequence it received via the on_* hooks.
Must match the simulation world (tests/simulation/event_channel/test_event_channel_sim.py)
— this is the dual-world parity proof for the event channel.

Also exercises request_session_end end-to-end: the bot ends the session itself
(no operator Ctrl+C), and a SESSION_END event is delivered before teardown.
"""

import shutil

import pytest

from python.configuration.autotrader.autotrader_config_loader import load_autotrader_config
from python.framework.autotrader.autotrader_main import AutotraderMain


MOCK_PROFILE = 'configs/autotrader_profiles/backtesting/event_channel_lifecycle.json'

# Must match the simulation world (test_event_channel_sim.py).
EXPECTED_EVENT_SEQUENCE = ['order_filled', 'partial_close', 'session_end']


@pytest.fixture(scope='module')
def session():
    """Run one full mock session and capture the probe's received-event log."""
    config = load_autotrader_config(MOCK_PROFILE)
    trader = AutotraderMain(config)
    result = trader.run()
    received = trader._decision_logic.get_received_event_log()
    run_dir = trader._run_dir
    yield result, received
    if run_dir and run_dir.exists():
        shutil.rmtree(run_dir)


def test_session_completes_without_errors(session):
    result, _ = session
    assert len(result.error_messages) == 0, result.error_messages


def test_event_sequence_matches_expected(session):
    _, received = session
    assert received == EXPECTED_EVENT_SEQUENCE


def test_order_filled_is_first(session):
    _, received = session
    assert received[0] == 'order_filled'


def test_session_end_delivered_via_request(session):
    """request_session_end ended the session cleanly — SESSION_END is the last event."""
    _, received = session
    assert received[-1] == 'session_end'
