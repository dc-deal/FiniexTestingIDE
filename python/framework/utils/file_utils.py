import re
from pathlib import Path

from python.framework.constants.run_layout_constants import SCENARIO_LOG_NAME_PATTERN
from python.framework.exceptions.data_home_errors import DirectoryCreationDeniedError


def sanitize_filename(name: str) -> str:
    """
    Sanitize a string to make it safe for use as a filename.
    Replaces all invalid characters with underscores.
    """
    # Windows forbidden characters: \ / : * ? " < > |
    # Also strip control characters
    name = re.sub(r'[\\/:*?"<>|\x00-\x1F]', '_', name)

    # Optional: strip leading/trailing whitespace
    name = name.strip()

    # Optional: collapse repeated underscores
    name = re.sub(r'_+', '_', name)

    # If everything is stripped → fallback
    if not name:
        name = '_'

    return name


def pad_int(value: int, width: int = 2) -> str:
    """
    Pad an integer with leading zeros up to the given width.
    Returns a string.
    """
    return str(value).zfill(width)


def file_name_for_scenario(scenario_index: int, name: str):
    return SCENARIO_LOG_NAME_PATTERN.format(
        scenario_no=pad_int(scenario_index + 1), scenario=sanitize_filename(name))


def create_directory(path: Path, holds: str, setting: str = '', mode: int = 0o777) -> Path:
    """
    Create a directory the IDE needs, with its parents — the one place the IDE does so.

    The IDE creates every directory it works in and needs access to all of them. When the
    operating system refuses, the refusal becomes a named error that says which path, why, and
    what the directory was to hold, instead of a bare traceback; a caller that can carry on
    without the directory still catches it as the OSError it also is.

    Args:
        path: The directory
        holds: What the directory is to hold, as the message names it
        setting: The configuration key that places the directory, when one does
        mode: The permission bits a newly created directory gets

    Returns:
        The directory
    """
    try:
        path.mkdir(mode=mode, parents=True, exist_ok=True)
    except OSError as error:
        raise DirectoryCreationDeniedError(
            _creation_denied_message(path, holds, setting, error)) from error
    return path


def _creation_denied_message(path: Path, holds: str, setting: str, error: OSError) -> str:
    """
    The message of a refused directory: the cause first, then the path, what it was to hold and
    where to look.

    Args:
        path: The directory that could not be created
        holds: What it was to hold
        setting: The configuration key that places it, or empty
        error: The operating system's refusal

    Returns:
        The message, three lines
    """
    cause = 'OS Permission Error' if isinstance(error, PermissionError) else 'OS Error'
    errno_part = f'[Errno {error.errno}] ' if error.errno is not None else ''
    reason = error.strerror or str(error)
    where = f'; the path comes from {setting}' if setting else ''
    return (f'{cause} — directory creation denied: {path}\n'
            f'{errno_part}{reason} — it was to hold {holds}.\n'
            f'Check that the IDE may write there{where}.')
