"""
FiniexTestingIDE - Kraken Error Codes

Kraken answers most failures with HTTP 200 and an `error` list in the body, so the transport's
classification (`http_attempt.send_classified`) never sees them. Nearly every entry IS the venue's
answer — `EOrder:Insufficient funds`, `EAPI:Invalid key` — and stays terminal: retrying it would
report their outage for our request. Three are documented as the venue not answering the request
at all, and go to the connection ladder as transient (#598):

- `EService:Unavailable` — the matching engine or API is offline, typically during maintenance
- `EService:Deadline elapsed` — the request ran out of time before the engine processed it; an
  order may have been placed all the same, which is why a write is asked about, never sent again
- `EGeneral:Internal error` — an unexpected error on Kraken's side, with an optional numeric suffix

Source: Kraken's documentation of its spot errors (docs.kraken.com, "Spot errors"), read
2026-10-20; it advises backoff and retry for the first and the last. What is matched is the
venue's error CODE, a documented identifier, never the text around it. A read retries within its
ladder's budget; a write comes back UNRESOLVED and is resolved by asking the venue.
"""

from typing import List

from python.framework.exceptions.connection_errors import ConnectionAttemptFailedError

# The codes Kraken documents as "not now" rather than "no"
KRAKEN_NOT_NOW_CODES = ('EService:Unavailable', 'EService:Deadline elapsed',
                        'EGeneral:Internal error')


def raise_for_kraken_errors(errors: List[str], label: str = 'Kraken API error') -> None:
    """
    Raise for an answer whose `error` list is not empty, classified for the connection ladder.

    Args:
        errors: The answer's `error` list
        label: How the message names the answer, for the line the operator reads

    Returns:
        None — an answer without errors passes
    """
    if not errors:
        return
    message = f'{label}: {errors}'
    if all(_is_not_now(error) for error in errors):
        raise ConnectionAttemptFailedError(message, terminal=False)
    raise ConnectionError(message)


def _is_not_now(error: str) -> bool:
    """
    Whether one entry of Kraken's error list is a documented "not now".

    Args:
        error: One entry, such as 'EService:Unavailable' or 'EGeneral:Internal error:5'

    Returns:
        True for a not-now code, with or without a suffix
    """
    return any(error == code or error.startswith(f'{code}:') for code in KRAKEN_NOT_NOW_CODES)
