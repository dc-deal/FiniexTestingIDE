"""
FiniexTestingIDE - Signal HTTP Reader
The one JSON read against the producer's HTTP routes, with its failure classification.

Shared by the connect check and the feed certificate rather than copied into each,
because the classification below is a contract obligation and not a convenience: the
producer's connect contract states that 401 is NOT a transport failure. A reader that
reports "unreachable" for a rejected token sends the operator looking at the wrong
system, and a second copy of that rule is a second place for it to drift.
"""

import json
import urllib.error
import urllib.request

from python.framework.types.signal_data_types import ProducerRead

# The contract's own wording: these mean "your credential", never "their outage".
CREDENTIAL_STATUS_CODES = (401, 403)


def fetch_json(url: str, token: str, timeout_s: float) -> ProducerRead:
    """
    Read one JSON route, sending the bearer token when one is configured.

    Args:
        url: Full route URL
        token: Bearer token; empty means send no Authorization header
        timeout_s: Request timeout

    Returns:
        The decoded payload, or the classified reason nothing came back
    """
    request = urllib.request.Request(url)
    if token:
        request.add_header('Authorization', f'Bearer {token}')

    try:
        with urllib.request.urlopen(request, timeout=timeout_s) as response:
            payload = json.loads(response.read().decode('utf-8'))
    except urllib.error.HTTPError as error:
        if error.code in CREDENTIAL_STATUS_CODES:
            return ProducerRead(
                ok=False,
                detail=f'{error.code} — the producer refused the credential. This is NOT '
                       f'an outage on their side; the token is missing, wrong or revoked.',
                credential_rejected=True,
                status_code=error.code)
        return ProducerRead(
            ok=False,
            detail=f'HTTP {error.code} — {error.reason}',
            status_code=error.code)
    except Exception as error:   # noqa: BLE001 — a diagnostic never crashes on its subject
        return ProducerRead(
            ok=False, detail=f'unreachable: {type(error).__name__} — {error}')

    return ProducerRead(ok=True, payload=payload)
