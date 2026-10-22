"""
FiniexTestingIDE - Credentials Cascade

Where a configured credentials file is read from: the operator's workspace copy first — under
the data home (`data_home.py`) — and the tracked default second. Every credential in this project is named by a path below the
credentials directory (`venues/kraken_credentials.json`, `peers/rag_credentials.json`), and this
is the one place that turns such a name into a file. A venue adapter, a peer client and the
operator alert channel ask here rather than carrying a copy of the rule — so an adapter moved
into a package of its own (#328) takes no copy with it, and the next adapter writes none.

The inbound files (the API's own tokens) add config isolation on top of this cascade; that
rule lives with them (`api_auth/inbound_credentials_cascade.py`).
"""

from pathlib import Path
from typing import Optional

from python.configuration.data_home import workspace_path

# The cascade every credential in this project uses, most specific first: the directory below
# the operator's workspace, then the tracked one.
WORKSPACE_CREDENTIALS_SUBDIR = 'credentials'
TRACKED_CREDENTIALS_DIR = 'configs/credentials'


def resolve_credentials_file(credentials_file: str) -> Optional[Path]:
    """
    The file a configured credentials name resolves to.

    Args:
        credentials_file: Path below the credentials directory, e.g.
            `venues/kraken_credentials.json`

    Returns:
        The workspace copy when it exists, else the tracked default when it exists, else None
    """
    for path in (workspace_credentials_path(credentials_file),
                 tracked_credentials_path(credentials_file)):
        if path.exists():
            return path
    return None


def workspace_credentials_path(credentials_file: str) -> Path:
    """
    Where the operator's own copy of a configured credentials file lives.

    The tracked default holds a placeholder the credential guard refuses, so "is there a real
    key on this machine" is a question about this path alone.

    Args:
        credentials_file: Path below the credentials directory

    Returns:
        The workspace path, whether or not it exists
    """
    return workspace_credentials_dir() / credentials_file


def workspace_credentials_dir() -> Path:
    """
    The operator's credentials directory, in the workspace under the data home.

    Returns:
        The directory, whether or not it exists
    """
    return workspace_path(WORKSPACE_CREDENTIALS_SUBDIR)


def tracked_credentials_path(credentials_file: str) -> Path:
    """
    Where the tracked default of a configured credentials file lives.

    Args:
        credentials_file: Path below the credentials directory

    Returns:
        The tracked path, whether or not it exists
    """
    return Path(TRACKED_CREDENTIALS_DIR) / credentials_file
