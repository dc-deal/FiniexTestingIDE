"""
The order-event stream as a test opens and reads it: a session's writer on a stream of its own at a
path the test chooses, and one stream file read back through the production parser — without a run
directory around either.
"""

from pathlib import Path
from typing import List, Optional, Tuple

from python.framework.constants.run_layout_constants import (
    ORDER_EVENTS_SCHEMA_VERSION,
    ORDER_EVENTS_STREAM,
)
from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.reporting.io.order_event_stream_io import order_events_of
from python.framework.reporting.io.order_event_stream_writer import OrderEventStreamWriter
from python.framework.reporting.io.run_stream_appender import RunStreamAppender
from python.framework.reporting.io.run_stream_reader import read_stream_lines
from python.framework.types.api.report_types import BrokerTruthRow, OrderEventRow
from python.framework.types.run_stream_types import RunStreamLines


def open_test_order_event_writer(path: Path, run_id: str, unit_name: str,
                                 logger: Optional[AbstractLogger]) -> OrderEventStreamWriter:
    """
    Open an order-event stream at a path, the way a session opens its own.

    Args:
        path: The stream's file
        run_id: The run, written into the header line
        unit_name: The unit every line is filed under
        logger: Where a failed write is said

    Returns:
        The writer
    """
    appender = RunStreamAppender(
        path, ORDER_EVENTS_STREAM, ORDER_EVENTS_SCHEMA_VERSION, run_id, logger,
        stops='The stream is incomplete from this event on', required=True)
    return OrderEventStreamWriter(appender, unit_name)


def read_order_event_file(
    path: Path,
) -> Tuple[List[OrderEventRow], List[BrokerTruthRow], bool]:
    """
    Read one order-event stream file back through the parser every reader of a run uses.

    Args:
        path: The stream's file

    Returns:
        (the order events in file order, the broker-truth lines in file order, whether the last
        line was cut off)
    """
    path = Path(path)
    stream_file = read_stream_lines(path)
    stream = RunStreamLines(
        header=stream_file.header, records=[line.record for line in stream_file.lines],
        truncated_tail=stream_file.truncated_tail,
        last_unterminated=stream_file.last_unterminated)
    return order_events_of(stream, path.name, str(path))
