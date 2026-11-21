"""
FiniexTestingIDE - The run index, written for a session

A session writes the run index three times: its row at the start, its streams as they open, the
artifacts at its end. Each is an update of a derived index, so a session waits for one only briefly
and never ends over it (#650): another process holding the index is waited for at most
SESSION_INDEX_LOCK_WAIT_S, then the update is given up with a warning. The run is recorded in its own
directory either way, and a rebuild of the index brings the row back.
"""

from typing import Callable

from python.configuration.app_config_manager import AppConfigManager
from python.framework.exceptions.file_lock_errors import FileLockHeldError
from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.reporting.store.run_index import RunIndex
from python.framework.store.abstract_store_index import SESSION_INDEX_LOCK_WAIT_S


def write_run_index_for_session(write: Callable[[RunIndex], None], logger: AbstractLogger,
                                what: str) -> None:
    """
    Make one update of the run index for a session; a held index costs the update, never the session.

    Args:
        write: The update, handed the index
        logger: Where a given-up update is reported
        what: What the update records, for the warning (`this session`, `its order-event stream`)
    """
    index = RunIndex(AppConfigManager().get_file_logging_config_object().run_index)
    index.set_lock_wait_s(SESSION_INDEX_LOCK_WAIT_S)
    try:
        write(index)
    except FileLockHeldError as error:
        logger.warning(
            f'⚠️ The run index did not record {what}: {error}. The run is recorded in its own '
            f'directory, and the index catches up with `python python/cli/store_cli.py rebuild runs`. '
            f'The session runs on.'
        )
