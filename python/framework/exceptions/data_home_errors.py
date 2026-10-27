"""
FiniexTestingIDE - Data Home Errors
Exception types for the directory the operator's state lives in (#598), and for the directories
the IDE creates below it (#621).
"""

from python.framework.exceptions.finiex_error import FiniexError


class DataHomeError(FiniexError):
    """
    The data home that FINIEX_DATA_HOME names cannot be used.

    Raised when the variable names a relative path, or a directory that does not exist. A missing
    home is never created on the fly: an empty one holds no carry-over, and a session started on it
    would begin as the bot's first run while its holding sits at the venue. The message names the
    variable, the path and the way forward.
    """
    pass


class DirectoryCreationDeniedError(FiniexError, OSError):
    """
    A directory the IDE needs could not be created: the operating system refused it — no
    permission, a read-only volume, a full disk, a file standing where the directory should be.

    The IDE creates every directory it works in, so this is a hard error for whatever needed the
    directory: a run does not proceed without its own. The message opens with the cause and names
    the path, what the directory was to hold and the setting that places it. An OSError too, so a
    caller that can carry on without its directory — the activity list, the liveness stamp — keeps
    catching it the way it caught the refusal before.
    """
    pass
