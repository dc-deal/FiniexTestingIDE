"""
FiniexTestingIDE - Inbound Credentials Cascade

Where the files under `credentials/inbound/` are read from: the API's consumer tokens and the
accounts those tokens act for (#551). Two files, maintained and revoked together, read through
ONE cascade — so the cascade, the isolation rule and the parse checks exist once.

Under config isolation only the tracked copy answers. A test run that read the operator's real
token file would depend on the workspace it happens to run in, and would load a live secret into
a process that has no business holding one.
"""

import json
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from python.framework.exceptions.api_errors import ApiConfigurationError
from python.framework.utils.config_merge_utils import (
    is_config_isolation_active,
    without_meta_keys,
)

# The cascade every credential in this project uses, most specific first.
WORKSPACE_CREDENTIALS_DIR = 'user_configs/credentials'
TRACKED_CREDENTIALS_DIR = 'configs/credentials'


def inbound_credential_dirs() -> Tuple[str, ...]:
    """
    The directories an inbound credentials file is looked up in, most specific first.

    Returns:
        Only the tracked directory under config isolation, else workspace then tracked
    """
    if is_config_isolation_active():
        return (TRACKED_CREDENTIALS_DIR,)
    return (WORKSPACE_CREDENTIALS_DIR, TRACKED_CREDENTIALS_DIR)


def read_inbound_section(relative_file: str,
                         section: str) -> Tuple[Dict[str, Any], Optional[str]]:
    """
    Read one section of the first inbound credentials file the cascade offers.

    Refused rather than repaired: invalid JSON, a key named twice (a JSON object keeps only the
    LAST of two equal keys, so the first entry would vanish without a word), a section that is
    not an object, and an entry that is not one. Documentation keys (`_comment`) are dropped
    from the section, so a file may explain itself at any level.

    Args:
        relative_file: Path below the credentials directory, e.g. `inbound/accounts.json`
        section: The top-level key holding the entries, e.g. `accounts`

    Returns:
        The section's entries and the path that answered, or ({}, None) when no file exists
    """
    for directory in inbound_credential_dirs():
        path = Path(directory) / relative_file
        if not path.exists():
            continue
        try:
            with open(path, 'r') as handle:
                raw = json.load(handle, object_pairs_hook=_refuse_repeated_keys)
        except json.JSONDecodeError as error:
            raise ApiConfigurationError(
                f'Invalid JSON in {path}\n{error}\nFix the syntax or remove the file.'
            )
        except ApiConfigurationError as error:
            # Raised by the parse hook, which does not know which file it is reading.
            raise ApiConfigurationError(f'{path} {error}')
        entries = raw.get(section, {}) if isinstance(raw, dict) else None
        if not isinstance(entries, dict):
            raise ApiConfigurationError(
                f'{path}: "{section}" must be an object keyed by name.')
        entries = without_meta_keys(entries)
        for name, entry in entries.items():
            if not isinstance(entry, dict):
                raise ApiConfigurationError(
                    f'{path}: the entry {name!r} under "{section}" is not an object.')
        return entries, str(path)
    return {}, None


def _refuse_repeated_keys(pairs: List[Tuple[str, Any]]) -> Dict[str, Any]:
    """
    Build a JSON object, refusing one that names a key twice.

    Args:
        pairs: The object's key/value pairs in file order

    Returns:
        The object as a dict
    """
    result: Dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ApiConfigurationError(
                f'names {key!r} twice. A JSON object keeps only the LAST of two equal keys, so '
                f'the first entry would be dropped without a word — keep one.')
        result[key] = value
    return result
