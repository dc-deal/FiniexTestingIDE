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

from python.framework.reporting.event_stream_csv_writer import EVENT_FIELDS, EventStreamWriter
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


class TestSideAndDirectionColumns:
    """`side` and `direction` are distinct CSV columns — FIX-style separation
    of OrdSide (BUY/SELL, per-trade operation) from PositionSide (LONG/SHORT,
    position view). FILL rows carry side; POSITION_OPEN/CLOSE rows carry direction."""

    def _col_indices(self):
        return EVENT_FIELDS.index('direction'), EVENT_FIELDS.index('side'), EVENT_FIELDS.index('event_type')

    def test_side_column_present_in_header(self, events_csv_rows):
        assert 'side' in EVENT_FIELDS
        assert events_csv_rows[0][EVENT_FIELDS.index('side')] == 'side'

    def test_fill_rows_carry_side_not_direction(self, events_csv_rows):
        dir_idx, side_idx, type_idx = self._col_indices()
        fill_rows = [r for r in events_csv_rows[1:] if r[type_idx] == 'FILL']
        assert len(fill_rows) > 0
        for row in fill_rows:
            assert row[dir_idx] == '', f"FILL row has direction={row[dir_idx]!r}"
            assert row[side_idx] in ('buy', 'sell'), f"FILL row side={row[side_idx]!r}"

    def test_position_rows_carry_direction_not_side(self, events_csv_rows):
        dir_idx, side_idx, type_idx = self._col_indices()
        pos_rows = [r for r in events_csv_rows[1:] if r[type_idx] in ('POSITION_OPEN', 'POSITION_CLOSE')]
        assert len(pos_rows) > 0
        for row in pos_rows:
            assert row[dir_idx] in ('long', 'short'), f"position row direction={row[dir_idx]!r}"
            assert row[side_idx] == '', f"position row has side={row[side_idx]!r}"

    def test_fill_side_matches_position_direction_semantically(self, events_csv_rows):
        """A FILL row's side must be consistent with the position direction
        learned from POSITION_OPEN: LONG positions have entry BUY + close SELL,
        SHORT positions have entry SELL + close BUY. This catches the latent
        Sim close-pending bug where direction=None defaulted into the wrong
        helper branch (regression guard against re-emergence)."""
        dir_idx, side_idx, type_idx = self._col_indices()
        pos_idx = EVENT_FIELDS.index('position_id')

        # Learn position direction from POSITION_OPEN events
        position_direction = {}
        for row in events_csv_rows[1:]:
            if row[type_idx] == 'POSITION_OPEN':
                position_direction[row[pos_idx]] = row[dir_idx]

        # For each FILL, verify side matches the (direction, action) mapping
        seen_open = set()
        for row in events_csv_rows[1:]:
            if row[type_idx] != 'FILL':
                continue
            pid = row[pos_idx]
            side = row[side_idx]
            if pid not in position_direction:
                continue  # FILL before POSITION_OPEN — skip
            direction = position_direction[pid]

            # First FILL per position = entry; subsequent = exits
            is_entry = pid not in seen_open
            if is_entry:
                seen_open.add(pid)
                expected = 'buy' if direction == 'long' else 'sell'
            else:
                expected = 'sell' if direction == 'long' else 'buy'

            assert side == expected, (
                f"FILL pos={pid} direction={direction} is_entry={is_entry} "
                f"expected side={expected!r}, got {side!r}"
            )
