"""
FiniexTestingIDE - Order-Event Stream Setup (#362)

Opens a session's order-event stream, registers it with the executor and names it in the run
index — what makes a session's order transitions readable while it runs.
"""

from pathlib import Path

from python.configuration.app_config_manager import AppConfigManager
from python.framework.reporting.io.order_event_stream_writer import OrderEventStreamWriter
from python.framework.reporting.store.report_store import IO_SUBDIR
from python.framework.reporting.store.run_index import RunIndex
from python.framework.trading_env.abstract_trade_executor import AbstractTradeExecutor


def open_order_event_stream(
    executor: AbstractTradeExecutor,
    run_dir: Path,
    run_id: str,
    unit_name: str,
) -> OrderEventStreamWriter:
    """
    Open the stream a session writes its order transitions into, as they happen.

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
    RunIndex(AppConfigManager().get_file_logging_config_object().run_index) \
        .record_streams(run_id, run_dir)
    return writer
