"""
FiniexTestingIDE - Decision Event Channel — Simulation Pipeline (#348)

Runs the event-probe decision logic through the backtesting (simulation)
pipeline and asserts the ordered sequence of decision events it received via
the on_* hooks. The same sequence is asserted by the AutoTrader-mock test
(tests/autotrader/integration/test_event_channel_live_pipeline.py) — proving the
channel behaves identically in both pipelines.
"""

import pytest

from tests.shared.fixture_helpers import (
    run_scenario,
    extract_process_result,
    extract_tick_loop_results,
    extract_backtesting_metadata,
)


EVENT_CHANNEL_CONFIG = "backtesting/event_channel_test.json"

# Must match the AutoTrader-mock world (test_event_channel_live_pipeline.py).
EXPECTED_EVENT_SEQUENCE = ['order_filled', 'partial_close', 'session_end']


@pytest.fixture(scope="module")
def received_events():
    """Run the event-probe scenario once and return the recorded event log."""
    summary = run_scenario(EVENT_CHANNEL_CONFIG)
    process_result = extract_process_result(summary)
    tick_loop_results = extract_tick_loop_results(process_result)
    metadata = extract_backtesting_metadata(tick_loop_results)
    return metadata.received_events


def test_event_sequence_matches_expected(received_events):
    assert received_events == EXPECTED_EVENT_SEQUENCE


def test_order_filled_is_first(received_events):
    assert received_events[0] == 'order_filled'


def test_partial_close_delivered(received_events):
    assert 'partial_close' in received_events


def test_session_end_is_last(received_events):
    assert received_events[-1] == 'session_end'
