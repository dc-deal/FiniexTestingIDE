"""
FiniexTestingIDE External History Exceptions

Raised when a vendor's published archive is not what this side expects — a part that differs from
its published checksum, a manifest of another schema, a member that is not the pair asked for.
The converter refuses rather than survives: an archive read wrongly without an error (a price where
a timestamp belongs) is the failure these exist to prevent.
"""

from python.framework.exceptions.finiex_error import FiniexError


class ExternalArchiveError(FiniexError, ValueError):
    """Raised when a vendor archive, its parts or one of its members cannot be trusted as read."""
