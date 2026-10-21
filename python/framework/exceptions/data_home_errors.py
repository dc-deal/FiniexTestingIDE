"""
FiniexTestingIDE - Data Home Errors
Exception types for the directory the operator's state lives in (#598).
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
