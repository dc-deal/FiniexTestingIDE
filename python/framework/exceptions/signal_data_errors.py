"""
FiniexTestingIDE - Signal Data Errors
Exception types for the SIGNAL worker / signal data layer (#141).
"""

from python.framework.exceptions.finiex_error import FiniexError


class SignalProviderNotInjectedError(FiniexError, RuntimeError):
    """
    A SIGNAL worker ran without an injected SignalDataProvider.

    The provider is built from the prepared signal series and injected at
    construction (sim subprocess / live boot). A missing provider is a wiring
    bug, never a silent fallback.
    """
    pass


class SignalSchemaError(FiniexError, ValueError):
    """
    An archived signal line declares an incompatible schema_version.

    The reader validates schema_version on read; a major-version mismatch means
    the result structure may have changed and is not safe to consume.
    """
    pass
