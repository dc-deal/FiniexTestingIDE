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


class SignalSourceUnresolvedError(FiniexError, ValueError):
    """
    A session has SIGNAL workers but no source that could feed them.

    Either the profile declares no mounted series and no live transport is enabled, or an
    enabled transport cannot serve the session (several signal kinds against one live
    source, #258; a transport that is configured but not built yet, #468).

    A configuration error, never a fallback: a session told to decide on signals must not
    silently proceed on whatever the archive happened to hold.
    """
    pass


class SignalDataUnavailableError(FiniexError, ValueError):
    """
    A scenario declares a SIGNAL source (#429) with no data covering its range.

    A config/data problem (wrong data_sentiment_type, un-imported source, or a range
    entirely outside the signal coverage), NOT a code bug. Per the batch error model,
    this excludes ONLY the offending scenario (ValidationResult) — the batch continues.
    A partial overlap is fine (sentiment resolves where available, stale beyond).
    """
    pass
