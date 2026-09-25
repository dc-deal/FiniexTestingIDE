"""
FiniexTestingIDE - Host Identity Errors
Exception types for the installation's minted identity (#551).
"""

from python.framework.exceptions.finiex_error import FiniexError


class HostIdentityError(FiniexError):
    """
    The installation's identity file exists but cannot be trusted.

    Raised when `user_configs/host_identity.json` is present and unreadable, is not valid JSON,
    or does not carry the minted shape. It is never answered by minting a new identity: a silent
    re-mint is an identity change nobody notices, and every run recorded afterwards would claim
    to come from a different machine. The start is refused instead, and the message names the
    file and the two ways forward — restore it, or delete it deliberately to mint a new one.
    """
    pass
