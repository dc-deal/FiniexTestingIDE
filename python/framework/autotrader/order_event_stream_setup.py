"""
FiniexTestingIDE - Order-Event Stream Setup (#362)

Opens a session's order-event stream, registers it with the executor and names it in the run
index — what makes a session's order transitions readable while it runs — and reads it back once
the session has ended, for what is derived from it.
"""

from pathlib import Path
from typing import List, Tuple

from python.configuration.app_config_manager import AppConfigManager
from python.framework.exceptions.report_artifact_errors import ReportArtifactUnreadableError
from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.reporting.io.artifact_specs import ORDER_EVENTS_STREAM
from python.framework.reporting.io.order_event_stream_io import (
    broker_truth_from_row,
    order_event_from_row,
    read_order_event_stream,
)
from python.framework.reporting.io.order_event_stream_writer import OrderEventStreamWriter
from python.framework.reporting.store.report_store import IO_SUBDIR
from python.framework.reporting.store.run_index import RunIndex
from python.framework.trading_env.abstract_trade_executor import AbstractTradeExecutor
from python.framework.types.live_types.broker_truth_types import BrokerTruthRecord
from python.framework.types.trading_env_types.order_event_types import OrderEvent


def open_order_event_stream(
    executor: AbstractTradeExecutor,
    run_dir: Path,
    run_id: str,
    unit_name: str,
) -> OrderEventStreamWriter:
    """
    Open the stream a session writes its order transitions into, as they happen — and what the
    venue reports when the session asks it, on the same counter.

    Opened once the executor exists — it is what records — and before the cold start, whose
    adoptions are the first steps a session can record. The run index learns of the stream in
    the same step, so the run list names it before any report exists: a session killed before
    its report still has every transition up to the moment it stopped.

    Args:
        executor: The session's executor, which records every order transition
        run_dir: The run's own directory
        run_id: The run the stream belongs to
        unit_name: The unit every line is filed under — the name the session's reports use

    Returns:
        The open writer; the session closes it at shutdown
    """
    # The executor's logger is the session channel, where a failed write has to be heard
    writer = OrderEventStreamWriter(run_dir / IO_SUBDIR, run_id, unit_name, executor.logger)
    executor.add_order_event_listener(writer)
    executor.add_broker_truth_listener(writer.write_broker_truth)
    RunIndex(AppConfigManager().get_file_logging_config_object().run_index) \
        .record_streams(run_id, run_dir)
    return writer


def read_back_order_event_stream(
    run_dir: Path,
    logger: AbstractLogger,
) -> Tuple[List[OrderEvent], List[BrokerTruthRecord]]:
    """
    The order events and the broker-truth lines a session wrote, read back once it has ended
    (#362) — one read of the file for both.

    The events are what the pending-order counters and the check that the stream holds every
    submission are derived from; the broker-truth lines are what the venue-account section is
    derived from. A stream that cannot be read yields nothing, and says why on the session's
    channel — the check then reports the submissions it cannot find.

    Args:
        run_dir: The run's own directory
        logger: The session's channel

    Returns:
        (the events, the broker-truth lines), each in the order it was recorded; both empty when
        there is no readable stream
    """
    path = run_dir / IO_SUBDIR / ORDER_EVENTS_STREAM
    if not path.exists():
        return [], []
    try:
        rows, truths, truncated = read_order_event_stream(path)
    except ReportArtifactUnreadableError as e:
        logger.error(f'❌ The order-event stream could not be read back: {e}')
        return [], []
    if truncated:
        logger.warning(
            f'⚠️ The order-event stream {path} ends in a line cut off mid-write — it is left out')
    return ([order_event_from_row(row) for row in rows],
            [broker_truth_from_row(truth) for truth in truths])
