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


class SignalStreamHttpError(FiniexError, RuntimeError):
    """
    The producer's stream answered with a status that is neither success nor a refusal.

    A genuine transport fault: the connection is retried with backoff. Refusals live
    elsewhere on purpose — a rejected credential and an unknown pipeline id both stop the
    transport, because retrying either forever reports their outage for our mistake.
    """
    pass


class SignalStreamSilenceError(FiniexError, RuntimeError):
    """
    The stream socket delivered nothing for the whole watchdog — the keep-alive interval
    the producer serves, times the local multiple.

    A CONNECTION diagnosis and never a freshness one: the keep-alive proves the socket is
    alive, so its absence proves the socket is not. A producer that has simply gone quiet
    keeps sending keep-alives, and that silence is the provider's staleness contract to
    report — not this.

    Raised from the read, because the socket's own timeout IS the watchdog. It covers the
    response head as well as the frames, so a producer that accepts a connection and then
    answers nothing trips it too.
    """
    pass


class SignalStreamFrameTooLargeError(FiniexError, ValueError):
    """
    One stream line grew past what any envelope could legitimately be.

    Not a hypothetical: the decoder holds an unterminated line until its newline arrives,
    so a producer emitting bytes without one — a runaway serializer, a truncated framing
    bug — would grow that buffer without bound. Over a thirty-day unattended session that
    ends the process, and it would end it for a reason nothing in the logs explains.

    Treated as a contract violation rather than a transport fault: they answered, and what
    they sent is not something this reader can be expected to hold.
    """
    pass


class SignalAlreadyImportedError(FiniexError, ValueError):
    """
    A day's parquet already exists and the import was not told to replace it.

    Its own class so the importer can treat it the way the tick importer treats its
    duplicates — a WARNING and a skipped file, never a run error. A re-import of an inbox
    whose days are already archived is the normal case, and reporting it as an error puts
    a healthy run into the error pot (§35).
    """
