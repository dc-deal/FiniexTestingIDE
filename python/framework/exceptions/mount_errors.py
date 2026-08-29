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


class ScenarioPackageMissingError(FiniexError):
    """
    Raised when the mount holds no data package for a scenario that reached validation.

    The package dict is keyed by the scenario's own index; a scenario either got a package
    or was excluded before this point, so a hole means the preparator and the consumer
    disagree about what was prepared. That is framework logic, not operator config (§33) —
    it propagates rather than excluding a scenario and hiding the inconsistency.
    """
    pass
