"""
FiniexTestingIDE - Order-Event Stream Writer (#362)

A live session's order-event stream, written one line per event as the executor records it —
its order events, and what the venue reported when the session asked it. The row each record
becomes is the stream IO module's; the file — its header line, a flushed line per record, the seal
of each trading day (#476) — is the run stream appender's.
"""

from pydantic import BaseModel

from python.framework.reporting.io.order_event_stream_io import (
    broker_truth_row,
    order_event_row,
)
from python.framework.reporting.io.run_stream_appender import RunStreamAppender
from python.framework.types.live_types.broker_truth_types import BrokerTruthRecord
from python.framework.types.trading_env_types.order_event_types import OrderEvent


class OrderEventStreamWriter:
    """
    Writes a unit's events to the run's stream as they are recorded — the live session's writer.

    Registered as an executor listener, so an event is on disk before the next one is recorded.
    It is called from inside a fill, so a failed write must not end there: the appender says the
    first one on the session's error channel and stops writing, because a line written after a
    broken one would turn a readable cut-off tail into a damaged file.

    Args:
        appender: The open stream — opened as required, since a session does not trade without its
            order record
        scenario_name: The unit every line is filed under
    """

    def __init__(self, appender: RunStreamAppender, scenario_name: str):
        self._appender = appender
        self._scenario_name = scenario_name

    def __call__(self, event: OrderEvent) -> None:
        """
        Write one event.

        Args:
            event: The recorded event
        """
        self._write(order_event_row(event, self._scenario_name), event.seq)

    def write_broker_truth(self, record: BrokerTruthRecord) -> None:
        """
        Write one broker-truth record — registered as the executor's broker-truth listener.

        Args:
            record: What the venue answered
        """
        self._write(broker_truth_row(record, self._scenario_name), record.seq)

    def close(self) -> None:
        """Close the stream; events recorded afterwards are not written."""
        self._appender.close()

    def _write(self, row: BaseModel, seq: int) -> None:
        """
        Append one line; the appender says the first failure and ends the writing.

        Args:
            row: The line as the API serves it
            seq: Its position in the stream, named in the report of a failure
        """
        self._appender.write(row.model_dump(mode='json'), at=f'seq {seq}')
