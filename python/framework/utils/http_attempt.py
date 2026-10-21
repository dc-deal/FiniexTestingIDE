"""
FiniexTestingIDE - HTTP Attempt

One HTTP request, its failure classified for the connection ladder (#473, #598). A `requests` call
fails with the library's own exceptions, and a ladder that has not registered them reads every one
as TERMINAL — so it gives up at the first timeout. Made through here, a transport fault and a status
worth retrying become a TRANSIENT `ConnectionAttemptFailedError`, and a refusal a TERMINAL one, by
the rule the order traffic already follows (`is_terminal_status`).

What the venue says inside a successful answer — an error list in the body — is the caller's to
judge; this unit only knows the transport.
"""

from typing import Callable

import requests

from python.framework.exceptions.connection_errors import ConnectionAttemptFailedError
from python.framework.utils.connection_ladder import is_terminal_status


def send_classified(send: Callable[[], requests.Response], endpoint: str) -> requests.Response:
    """
    Make one request; a failure raises ConnectionAttemptFailedError, classified for the ladder.

    Args:
        send: The request to make
        endpoint: What it asks for, for the message the operator reads

    Returns:
        The response, already status-checked
    """
    try:
        response = send()
        response.raise_for_status()
        return response
    except requests.exceptions.HTTPError as error:
        status = error.response.status_code if error.response is not None else 0
        raise ConnectionAttemptFailedError(
            f'HTTP {status} from {endpoint}',
            terminal=is_terminal_status(status)) from error
    except requests.exceptions.RequestException as error:
        raise ConnectionAttemptFailedError(
            f'{type(error).__name__} on {endpoint}: {error}', terminal=False) from error
