"""
FiniexTestingIDE - Order-Event Stream Writer (#362)

A live session's order-event stream, written one line per event as the executor records it —
its order events, and what the venue reported when the session asked it. The file's format —
the header line, the row each record becomes — is the stream IO module's.
"""

from pathlib import Path

from pydantic import BaseModel

from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.reporting.io.artifact_specs import ORDER_EVENTS_STREAM
from python.framework.reporting.io.jsonl_stream_writer import JsonlStreamWriter
from python.framework.reporting.io.order_event_stream_io import (
    broker_truth_row,
    order_event_header,
    order_event_row,
)
from python.framework.types.live_types.broker_truth_types import BrokerTruthRecord
from python.framework.types.trading_env_types.order_event_types import OrderEvent


class OrderEventStreamWriter:
    """
    Writes a unit's events to the run's stream as they are recorded — the live session's writer.

    Registered as an executor listener, so an event is on disk before the next one is recorded.
    It is called from inside a fill, so a failed write must not end there: the first one is
    reported on the session's error channel and writing stops, because a line written after a
    broken one would turn a readable cut-off tail into a damaged file.

    Args:
        io_dir: The run's io/ directory
        run_id: The run the stream belongs to
        scenario_name: The unit every line is filed under
        logger: Where a failed write is reported — the session's own channel
    """

    def __init__(self, io_dir: Path, run_id: str, scenario_name: str, logger: AbstractLogger):
        self._scenario_name = scenario_name
        self._logger = logger
        self._failed = False
        self._writer = JsonlStreamWriter(Path(io_dir) / ORDER_EVENTS_STREAM)
        self._writer.write(order_event_header(run_id))

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
        """Close the file; events recorded afterwards are not written."""
        self._writer.close()

    def _write(self, row: BaseModel, seq: int) -> None:
        """
        Append one line; the first failure is reported and ends the writing.

        Args:
            row: The line as the API serves it
            seq: Its position in the stream, named in the report of a failure
        """
        if self._failed:
            return
        try:
            self._writer.write(row.model_dump(mode='json'))
        except OSError as e:
            self._failed = True
            self._logger.error(
                f'❌ The order-event stream {self._writer.get_path()} could not be written at '
                f'seq {seq}: {e}. The stream is incomplete from this event on; the '
                f'session continues.')
