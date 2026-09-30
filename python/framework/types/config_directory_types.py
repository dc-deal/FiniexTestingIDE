"""
FiniexTestingIDE - Config Directory Types (#554)

The vocabulary of the directory of every configuration that can start a run — what a file IS,
where it came from, and whether it could be read. The served models are in
`framework/types/api/directory_types.py`.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import List


class ConfigKind(StrEnum):
    """Which pipeline a configuration file starts."""
    SCENARIO_SET = 'scenario_set'
    AUTOTRADER_PROFILE = 'autotrader_profile'


class ConfigOrigin(StrEnum):
    """
    Where a configuration file lives — in the order a same-named file wins, highest first.

    The order is the scenario-set resolver's (`scenario_set_resolver.py`): a file the operator put
    in `user_configs/` shadows one in `user_algos/`, which shadows the shipped one in `configs/`.
    """
    USER_CONFIGS = 'user_configs'
    USER_ALGOS = 'user_algos'
    CONFIGS = 'configs'


class ConfigReadStatus(StrEnum):
    """
    Whether a file could be READ — never whether it is VALID.

    Validation happens at run start (the loader, then the pre-run phases); the directory reads
    files and says what they declare. A file being edited is `unreadable` for minutes at a time,
    and that is a state to show, never an error to raise.

    `unreadable` also covers the one fact about a file that its own bytes cannot show: its NAME is
    taken by a configuration of the other kind. A run records its configuration by file name, so
    such a file could not start a run anyone could trace — the loaders refuse it, and the row
    says why.
    """
    READABLE = 'readable'
    UNREADABLE = 'unreadable'
    # Parsed, but carries neither marker — a JSON file that is not a run configuration (an
    # analysis result beside a strategy, say). Cached so it is not read again, never served.
    NOT_A_CONFIG = 'not_a_config'


@dataclass
class DiscoveredConfigFile:
    """
    One candidate file, found by the directory's walk — only what a `stat` knows.

    Args:
        path: Where the file lives
        origin: Which root it was found under
        folder: Its sub-folder inside a `configs/` root ('' at the root, and always '' for the
            operator's own roots, whose layout is not served)
        mtime: Its modification time, the cache key's first half
        size: Its size in bytes, the cache key's second half
        shadowed: The origins of same-named files this one wins over
        shadowed_files: Those files themselves — read only to tell whether one of them is a
            configuration of the OTHER kind, which makes the name ambiguous
    """
    path: Path
    origin: ConfigOrigin
    folder: str
    mtime: float
    size: int
    shadowed: List[ConfigOrigin] = field(default_factory=list)
    shadowed_files: List['DiscoveredConfigFile'] = field(default_factory=list)


@dataclass
class ConfigValidationResult:
    """
    One scenario set, run through the real loader — the check the directory deliberately does not
    make on every read.

    Args:
        file: The file name
        ok: Whether the loader accepted it
        scenarios: How many scenarios it loaded (disabled ones excluded)
        reason: The loader's refusal, '' when accepted
    """
    file: str
    ok: bool
    scenarios: int = 0
    reason: str = ''
