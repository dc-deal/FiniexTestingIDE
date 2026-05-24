"""
FiniexTestingIDE - Baseline Event-Stream CSV Smoke Test (#330 / #233)

Minimal regression guard: an everyday sim run produces a parseable
event-stream CSV with chronological rows and the canonical header.
Cheaper than the per-event-type matrix in the partial_close suite.
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
    """Flush baseline trades/orders to a tempfile and parse the rows."""
    with tempfile.TemporaryDirectory() as tmp:
        run_dir = Path(tmp)
        EventStreamWriter.from_sim_result(
            trade_history=trade_history,
            order_history=order_history,
            run_dir=run_dir,
        ).flush('events.csv')

        with open(run_dir / 'events.csv') as f:
            return list(csv.reader(f))


class TestEventStreamMinimal:
    """Smoke-level guarantees for the everyday sim path."""

    def test_header_is_canonical(self, events_csv_rows):
        assert tuple(events_csv_rows[0]) == EVENT_FIELDS

    def test_has_data_rows(self, events_csv_rows):
        assert len(events_csv_rows) > 1, 'baseline run produced no events'

    def test_timestamps_monotonic(self, events_csv_rows):
        timestamps = [r[0] for r in events_csv_rows[1:]]
        assert timestamps == sorted(timestamps)

    def test_each_row_has_all_columns(self, events_csv_rows):
        """No row truncation — every event row matches EVENT_FIELDS arity."""
        for row in events_csv_rows[1:]:
            assert len(row) == len(EVENT_FIELDS)
