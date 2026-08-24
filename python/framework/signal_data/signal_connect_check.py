"""
FiniexTestingIDE - Signal Connect Check
One-shot reachability and credential probe against the producer (#98 connect contract).

Answers the question a live session cannot answer cheaply: does this address, with this
token, actually reach the producer — and WHICH producer. It performs exactly the two reads
the contract declares free (`/v1/health`, `/v1/pipelines/{id}/latest`) and never the paid
run route, so the check itself can never cost money.

The credential distinction is the point: the producer's contract states that 401 is not a
transport failure. A check that reports "unreachable" for a rejected token sends the
operator looking at the wrong system.
"""

import json
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

from python.framework.types.signal_data_types import (
    ConnectCheckResult,
    ConnectCheckStep,
)

# The contract's own wording: these mean "your credential", never "their outage".
CREDENTIAL_STATUS_CODES = (401, 403)


def _fetch(url: str, token: str, timeout_s: float) -> Dict[str, Any]:
    """
    Read one JSON route, sending the bearer token when one is configured.

    Args:
        url: Full route URL
        token: Bearer token; empty means send no Authorization header
        timeout_s: Request timeout

    Returns:
        The decoded response
    """
    request = urllib.request.Request(url)
    if token:
        request.add_header('Authorization', f'Bearer {token}')
    with urllib.request.urlopen(request, timeout=timeout_s) as response:
        return json.loads(response.read().decode('utf-8'))


def _probe(name: str, url: str, token: str, timeout_s: float,
           result: ConnectCheckResult, describe) -> Optional[Dict[str, Any]]:
    """
    Probe one route and record the outcome on the result.

    Args:
        name: Route label for the operator
        url: Full route URL
        token: Bearer token; empty means send no Authorization header
        timeout_s: Request timeout
        result: Result being accumulated
        describe: Callable turning a successful payload into one summary line

    Returns:
        The payload on success, None otherwise
    """
    try:
        payload = _fetch(url, token, timeout_s)
    except urllib.error.HTTPError as error:
        if error.code in CREDENTIAL_STATUS_CODES:
            result.credential_rejected = True
            result.steps.append(ConnectCheckStep(
                name, False,
                f'{error.code} — the producer refused the credential. This is NOT an '
                f'outage on their side; the token is missing, wrong or revoked.'))
        else:
            result.steps.append(ConnectCheckStep(
                name, False, f'HTTP {error.code} — {error.reason}'))
        return None
    except Exception as error:   # noqa: BLE001 — a diagnostic never crashes on its subject
        result.steps.append(ConnectCheckStep(
            name, False, f'unreachable: {type(error).__name__} — {error}'))
        return None

    result.steps.append(ConnectCheckStep(name, True, describe(payload), payload))
    return payload


def run_connect_check(
    base_url: str,
    pipeline_id: str,
    token: str,
    credential_source: str,
    timeout_s: float = 10.0,
) -> ConnectCheckResult:
    """
    Probe the producer's two free routes and report what answered.

    Args:
        base_url: Producer address, hostname form preferred over a raw address
        pipeline_id: Source to read one envelope from; empty skips that step
        token: Bearer token; empty means send no Authorization header
        credential_source: Where the token came from, for the operator's benefit
        timeout_s: Per-request timeout

    Returns:
        The accumulated result
    """
    root = base_url.rstrip('/')
    result = ConnectCheckResult(
        base_url=root,
        credential_source=credential_source,
        credential_configured=bool(token))

    def describe_health(payload: Dict[str, Any]) -> str:
        journal = payload.get('journal_id') or 'none'
        environment = payload.get('environment') or 'unknown'
        version = payload.get('version') or 'unknown'
        return f'journal {journal} ({environment}) · engine {version}'

    def describe_latest(payload: Dict[str, Any]) -> str:
        return (f"seq {payload.get('seq')} · epoch {payload.get('stream_epoch')} · "
                f"schema {payload.get('schema_version')} · "
                f"origin {payload.get('data_origin')}")

    # /v1/health is the one documented no-token route, so it is probed without one:
    # a failure here is the address, a failure on /latest alone is the credential.
    _probe('GET /v1/health', f'{root}/v1/health', '', timeout_s, result, describe_health)

    if pipeline_id:
        _probe(f'GET /v1/pipelines/{pipeline_id}/latest',
               f'{root}/v1/pipelines/{pipeline_id}/latest',
               token, timeout_s, result, describe_latest)

    return result


def print_connect_check(result: ConnectCheckResult) -> None:
    """
    Render one connect check for the operator.

    Args:
        result: The accumulated check outcome
    """
    print()
    print('=' * 72)
    print('📡 PRODUCER CONNECT CHECK')
    print('=' * 72)
    print(f'   Address:    {result.base_url}')
    print(f'   Credential: {result.credential_source}')
    if not result.credential_configured:
        print('               ⚠️  empty — no Authorization header is sent')
    print('-' * 72)
    for step in result.steps:
        mark = '✅' if step.ok else '❌'
        print(f'   {mark} {step.name}')
        print(f'      {step.detail}')
    print('-' * 72)
    if result.credential_rejected:
        print('   ❌ CREDENTIAL REJECTED — not a producer outage.')
        print('      Place a valid token in user_configs/credentials/ and re-run.')
    elif result.is_ok():
        print('   ✅ Reachable and authenticated.')
    else:
        print('   ❌ Not reachable as configured — see the failing step above.')
    print('=' * 72)
    print()
