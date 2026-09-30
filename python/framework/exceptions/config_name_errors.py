"""
FiniexTestingIDE - Config Name Errors

ConfigNameConflictError: a scenario set and an AutoTrader profile share one file name. A run
records its configuration by that name alone (`config_snapshot`), so the two could never be told
apart afterwards — the run is refused before it starts, and one of the files has to be renamed.
"""

from python.framework.exceptions.finiex_error import FiniexError


class ConfigNameConflictError(FiniexError, ValueError):
    """A configuration's file name is also the name of a configuration of the other kind."""
