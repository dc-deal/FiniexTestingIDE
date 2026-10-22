"""
FiniexTestingIDE - Data Home

Where the operator's state lives, apart from the code that reads it. A code tree holds what a
release tag carries — `configs/`, `docs/`, the code. The data home holds what a deploy into a new
tree must find again: the workspace (`user_configs/`), the strategy directories, and every store
the application writes — the carry-over, the records, the logs, the caches and the archive (#598).

One environment variable names it, `FINIEX_DATA_HOME`. Unset, the working directory is the home and
every path stays exactly as configured — relative and unchanged — so a development tree and the
test suite behave as before. A variable rather than a configuration key: the tracked configuration
cannot know a server's path, and the operator's override file lies inside the home it would have to
name. It is read whenever a path is asked for, so a test can set it for one case.
"""

import os
from pathlib import Path
from typing import Optional, Set, TypeVar

from python.framework.exceptions.data_home_errors import DataHomeError

DATA_HOME_ENV = 'FINIEX_DATA_HOME'

# The operator's workspace below the home: overrides, credentials, profiles, the host identity.
# Declared once — every workspace path is built from it here.
WORKSPACE_DIR = 'user_configs'

# A path keeps the type it came in, so a getter that returned a string still returns one.
PathT = TypeVar('PathT', str, Path)

# The named homes this process has already checked; a getter is asked often, and the check is a stat.
_checked_homes: Set[str] = set()


def get_data_home() -> Optional[Path]:
    """
    The data home FINIEX_DATA_HOME names. A relative path or a directory that does not exist
    raises DataHomeError, once per process and value.

    Returns:
        The named directory, or None when the variable is unset and the working directory is the home
    """
    value = os.environ.get(DATA_HOME_ENV, '').strip()
    if not value:
        return None
    home = Path(value).expanduser()
    if value in _checked_homes:
        return home

    if not home.is_absolute():
        raise DataHomeError(
            f'{DATA_HOME_ENV} names a relative path ({value}). A data home must be absolute: a '
            f'relative one moves with the working directory, which is what the home exists to '
            f'prevent.')
    if not home.is_dir():
        raise DataHomeError(
            f'{DATA_HOME_ENV} names {home}, which is not an existing directory. A data home is '
            f'never created on the fly — an empty one holds no carry-over, and a session started '
            f'on it would begin as the bot\'s first run. Point {DATA_HOME_ENV} at the home the last '
            f'session wrote, or create the directory deliberately for a first installation.')
    _checked_homes.add(value)
    return home


def resolve_in_data_home(path: PathT) -> PathT:
    """
    A configured path, placed under the data home.

    Args:
        path: A path as configured — relative to the home, or absolute

    Returns:
        The path unchanged when it is absolute or no home is named; otherwise the home joined with
        it, in the type it came in
    """
    home = get_data_home()
    if home is None or Path(path).is_absolute():
        return path
    joined = home / path
    return str(joined) if isinstance(path, str) else joined


def workspace_path(relative: str = '') -> Path:
    """
    A path in the operator's workspace, under the data home.

    Args:
        relative: Path below the workspace, e.g. `app_config.json` or `credentials/venues/x.json`;
            empty for the workspace itself

    Returns:
        The path, whether or not it exists — relative when no home is named
    """
    workspace = Path(WORKSPACE_DIR)
    return resolve_in_data_home(workspace / relative if relative else workspace)


def resolve_strategy_file(path: str) -> Path:
    """
    The file a strategy path in a profile or scenario set names — a decision logic or a worker.

    A relative path is looked up in the data home first, then in the code tree: the operator's
    strategies live in the home, while a shipped profile may name a file the tree carries (a test
    fixture). Without a named home it is the working directory, as before.

    Args:
        path: The path as configured

    Returns:
        The absolute, resolved path, whether or not the file exists
    """
    candidate = Path(path)
    if candidate.is_absolute():
        return candidate.resolve()
    home = get_data_home()
    if home is not None and (home / candidate).exists():
        return (home / candidate).resolve()
    return (Path.cwd() / candidate).resolve()


def describe_data_home() -> str:
    """
    One line naming the data home and where it came from, for a boot log.

    Returns:
        The line
    """
    home = get_data_home()
    if home is None:
        return f'Data home: the working directory {Path.cwd()} ({DATA_HOME_ENV} is not set)'
    return f'Data home: {home} (named by {DATA_HOME_ENV})'
