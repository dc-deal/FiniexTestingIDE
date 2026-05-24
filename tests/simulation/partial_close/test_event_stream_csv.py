"""
FiniexTestingIDE - Sim Event-Stream CSV Writer Tests (#330 / #233)

Builds the event stream from the partial_close scenario's terminal state
(trade_history + order_history fixtures) and verifies CSV shape, event-type
taxonomy, and the CLOSE_SUBMIT-vs-ORDER_SUBMIT separation that distinguishes
algo-decided closes from opens.

Uses tempfile to flush — the sim test fixtures don't go through
BatchReportCoordinator so we exercise EventStreamWriter directly.
"""

import csv
import tempfile
from pathlib import Path
from typing import List

import pytest

from python.framework.reporting.trade_log_csv_writer import EVENT_FIELDS, EventStreamWriter
from python.framework.types.portfolio_types.portfolio_trade_record_types import TradeRecord
from python.framework.types.trading_env_types.order_types import OrderResult


@pytest.fixture(scope='session')
def events_csv_rows(
    trade_history: List[TradeRecord],
    order_history: List[OrderResult]
) -> List[List[str]]:
    """Flush the partial_close scenario's terminal state to a tempfile and
    parse the rows. Session-scoped — the heavy scenario run is shared."""
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp)
        EventStreamWriter.from_sim_result(
            trade_history=trade_history,
            order_history=order_history,
            run_dir=run_dir,
        ).flush('events.csv')

        with open(run_dir / 'events.csv') as f:
            return list(csv.reader(f))


class TestCsvShape:
    """Schema contract: header matches canonical column tuple."""

    def test_header_is_canonical(self, events_csv_rows):
        assert tuple(events_csv_rows[0]) == EVENT_FIELDS

    def test_has_data_rows(self, events_csv_rows):
        assert len(events_csv_rows) > 1


class TestEventTaxonomy:
    """All expected event types present after a partial_close run."""

    def _event_types(self, rows):
        return [r[1] for r in rows[1:]]

    def test_order_submit_present(self, events_csv_rows):
        assert 'ORDER_SUBMIT' in self._event_types(events_csv_rows)

    def test_close_submit_present(self, events_csv_rows):
        """Distinct CLOSE_SUBMIT events for the algo-driven partial closes."""
        assert 'CLOSE_SUBMIT' in self._event_types(events_csv_rows)

    def test_position_open_present(self, events_csv_rows):
        assert 'POSITION_OPEN' in self._event_types(events_csv_rows)

    def test_position_close_present(self, events_csv_rows):
        assert 'POSITION_CLOSE' in self._event_types(events_csv_rows)

    def test_fill_present(self, events_csv_rows):
        assert 'FILL' in self._event_types(events_csv_rows)


class TestEventCounts:
    """Counts match what partial_close_test produces: 2 positions, 4 trades."""

    def _event_types(self, rows):
        return [r[1] for r in rows[1:]]

    def test_two_positions_opened(self, events_csv_rows):
        """One POSITION_OPEN per unique position (pos_usdjpy_1 + pos_usdjpy_2)."""
        events = self._event_types(events_csv_rows)
        assert events.count('POSITION_OPEN') == 2

    def test_four_positions_closed(self, events_csv_rows):
        """3 PARTIAL records for pos_usdjpy_1 + 1 FULL record for pos_usdjpy_2."""
        events = self._event_types(events_csv_rows)
        assert events.count('POSITION_CLOSE') == 4

    def test_close_submit_per_close_event(self, events_csv_rows):
        """One CLOSE_SUBMIT per close (3 partials of pos_1 + 1 full of pos_2)."""
        events = self._event_types(events_csv_rows)
        assert events.count('CLOSE_SUBMIT') == 4


class TestChronologicalOrdering:
    """Rows are sorted by timestamp (sort defended in flush())."""

    def test_timestamps_monotonic(self, events_csv_rows):
        timestamps = [r[0] for r in events_csv_rows[1:]]
        assert timestamps == sorted(timestamps)


class TestCloseSubmitDistinctFromOrderSubmit:
    """CLOSE_SUBMIT and ORDER_SUBMIT are separate event types — close events
    are no longer subsumed into ORDER_SUBMIT (the Option 1 fix)."""

    def test_both_event_types_emitted(self, events_csv_rows):
        events = [r[1] for r in events_csv_rows[1:]]
        assert events.count('ORDER_SUBMIT') > 0
        assert events.count('CLOSE_SUBMIT') > 0

    def test_open_and_close_share_order_id_but_different_events(self, events_csv_rows):
        """Same order_id can produce both ORDER_SUBMIT (open) and CLOSE_SUBMIT
        (close) — the dedup key is (order_id, action), not order_id alone."""
        order_id_to_events = {}
        for r in events_csv_rows[1:]:
            ts, event_type, order_id = r[0], r[1], r[2]
            if event_type in ('ORDER_SUBMIT', 'CLOSE_SUBMIT'):
                order_id_to_events.setdefault(order_id, set()).add(event_type)
        # At least one order_id has both event types
        assert any(
            evs == {'ORDER_SUBMIT', 'CLOSE_SUBMIT'}
            for evs in order_id_to_events.values()
        )
