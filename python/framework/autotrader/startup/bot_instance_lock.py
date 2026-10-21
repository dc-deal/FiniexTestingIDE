"""
FiniexTestingIDE - Bot Instance Lock

One bot runs once (#598). Two sessions of one bot on one account adopt the same resting orders, mint
position ids from their own counters and write one carry-over — the last writer wins, and at spot
the lost position book was the only record of a holding. So every AutoTrader session takes a kernel
lock on a file beside its carry-over, named by the same identity, before anything is read or sent,
and holds it until it has ended; a second start of the same bot is refused while the first lives.
A mock session and a dry run take it too: they write the risk baseline and the drawdown curve, and
two sessions of one bot that write nothing would still trade one account twice.

The lock lives as long as the session, not as one step: the rehearsed restart of #476 runs inside
the process and reads the carry-over again without letting go. Under a data home every code tree of
the machine resolves the same file, so a deploy's new tree is refused until the old session has
ended. The file's note names the holder — read for the refusal message, never decided on.
"""

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from python.configuration.data_home import describe_data_home, resolve_in_data_home
from python.framework.exceptions.file_lock_errors import FileLockHeldError
from python.framework.exceptions.persistence_errors import BotAlreadyRunningError
from python.framework.persistence.carry_over_identity import carry_over_key
from python.framework.types.autotrader_types.autotrader_config_types import AutoTraderConfig
from python.framework.utils.file_lock import FileLock, read_lock_note


def bot_lock_path(config: AutoTraderConfig) -> Path:
    """
    The lock file of a session's bot: beside its carry-over, under the same identity.

    Args:
        config: The resolved profile — its `cold_start` root and the bot's identity

    Returns:
        The lock file's path, whether or not it exists
    """
    root = resolve_in_data_home(Path(config.cold_start.path))
    key = carry_over_key(config.get_unit_name(), config.symbol, config.bot_id)
    return root / f'{key}.lock'


def take_bot_lock(config: AutoTraderConfig) -> FileLock:
    """
    Take the bot's lock for this session, or refuse the start while another session holds it.

    Args:
        config: The resolved profile

    Returns:
        The held lock — released by the session once it has written everything
    """
    path = bot_lock_path(config)
    try:
        lock = FileLock(path)
    except FileLockHeldError:
        raise BotAlreadyRunningError(_refusal(config, path)) from None
    lock.write_note(_note(run_id=''))
    return lock


def record_holder_run(lock: FileLock, run_id: str) -> None:
    """
    Add the session's run id to the lock's note, once it has been minted.

    Args:
        lock: The held lock
        run_id: The session's run id

    Returns:
        None
    """
    lock.write_note(_note(run_id))


def _note(run_id: str) -> str:
    """
    The holder, as a refused start will name it.

    Args:
        run_id: The holder's run id; '' before it is minted

    Returns:
        The note as JSON
    """
    return json.dumps({'pid': os.getpid(), 'run_id': run_id,
                       'since': datetime.now(timezone.utc).isoformat(timespec='seconds')})


def _refusal(config: AutoTraderConfig, path: Path) -> str:
    """
    The message a refused second start ends with.

    Args:
        config: The resolved profile
        path: The held lock file

    Returns:
        The message, naming the holder as far as its note says
    """
    try:
        holder = json.loads(read_lock_note(path) or '{}')
    except ValueError:
        holder = {}
    run = holder.get('run_id') or 'run id not yet minted'
    process = holder.get('pid', 'unknown')
    bot = config.bot_id or config.get_unit_name()
    return (
        f"STARTUP REFUSED — bot '{bot}' on {config.symbol} is already running ({run}, process "
        f'{process} on this host). Stop that session first; two sessions of one bot would '
        f"overwrite each other's carry-over. Lock: {path} ({describe_data_home()}).")
