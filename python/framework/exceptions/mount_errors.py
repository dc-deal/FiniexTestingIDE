"""
Mount errors (#417) — failures around the reusable data mount (the prepare/execute seam).
"""

from python.framework.exceptions.finiex_error import FiniexError


class MountIdentityMismatchError(FiniexError):
    """
    Raised when execute() is fed scenarios whose data identity does not match the mount.

    A mount holds data for a specific (broker, symbol, window, warmup, budget) identity;
    feeding it a scenarios set with a different identity would run the wrong data against the
    wrong parameters. Guards the #419/#418 reuse path — in #417's cold path the scenarios
    built the mount, so it never fires there.
    """
    pass
