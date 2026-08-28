"""
FiniexTestingIDE - Signal Connect Check
One-shot reachability and credential probe against the producer (#98 connect contract).

Answers the question a live session cannot answer cheaply: does this address, with this
token, actually reach the producer — and WHICH producer. It performs exactly the free reads
the contract declares free (`/v1/health`, `/v1/pipelines`, `/v1/pipelines/{id}/latest`)
and never the paid run route, so the check itself can never cost money.

The credential distinction is the point: the producer's contract states that 401 is not a
transport failure. A check that reports "unreachable" for a rejected token sends the
operator looking at the wrong system.
"""

from typing import Any, Dict, Optional

from python.framework.signal_data.producer.signal_http_reader import fetch_json
from python.framework.signal_data.producer.signal_pipelines_reader import (
    PIPELINES_ROUTE,
    describe_registry,
    fetch_pipeline_registry,
)
from python.framework.types.config_types.sentiment_config_types import ActiveProducer
from python.framework.types.signal_data_types import (
    ConnectCheckResult,
    ConnectCheckStep,
)


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
    read = fetch_json(url, token, timeout_s)
    if not read.ok:
        if read.credential_rejected:
            result.credential_rejected = True
        result.steps.append(ConnectCheckStep(name, False, read.detail))
        return None

    result.steps.append(
        ConnectCheckStep(name, True, describe(read.payload), read.payload))
    return read.payload


def run_connect_check(
    producer: ActiveProducer,
    pipeline_id: str,
    timeout_s: float = 10.0,
) -> ConnectCheckResult:
    """
    Probe the producer's free routes and report what answered.

    Args:
        producer: Active endpoint with its resolved credential
        pipeline_id: Source to read one envelope from; empty skips that step
        timeout_s: Per-request timeout

    Returns:
        The accumulated result
    """
    root = producer.base_url.rstrip('/')
    token = producer.credential.token
    result = ConnectCheckResult(
        endpoint_name=producer.name,
        base_url=root,
        credential_source=producer.credential.describe_source(),
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
    # a failure here is the address, a failure on a gated route alone is the credential.
    _probe('GET /v1/health', f'{root}/v1/health', '', timeout_s, result, describe_health)

    _check_registry(producer, pipeline_id, timeout_s, result)

    if pipeline_id:
        _probe(f'GET /v1/pipelines/{pipeline_id}/latest',
               f'{root}/v1/pipelines/{pipeline_id}/latest',
               token, timeout_s, result, describe_latest)

    return result


def _check_registry(
    producer: ActiveProducer,
    pipeline_id: str,
    timeout_s: float,
    result: ConnectCheckResult,
) -> None:
    """
    Read the pipeline registry and confirm the configured pipeline is in it.

    Worth its own step because it answers before a session what the stream would otherwise
    answer as a 404 mid-run: a misspelled pipeline id. It also reports whether the engine
    yet serves the keep-alive interval and replay window the push transport reads instead
    of configuring.

    Args:
        producer: Active endpoint with its resolved credential
        pipeline_id: Source the session is configured for; empty skips the membership check
        timeout_s: Request timeout
        result: Result being accumulated
    """
    registry = fetch_pipeline_registry(producer, timeout_s)
    if not registry.ok:
        if registry.credential_rejected:
            result.credential_rejected = True
        result.steps.append(
            ConnectCheckStep(f'GET {PIPELINES_ROUTE}', False, registry.detail))
        return

    summary = describe_registry(registry)
    if pipeline_id and pipeline_id not in registry.pipelines:
        known = ', '.join(sorted(registry.pipelines)) or '(none registered)'
        result.steps.append(ConnectCheckStep(
            f'GET {PIPELINES_ROUTE}', False,
            f"'{pipeline_id}' is not registered with this producer. Known: {known}"))
        return

    result.steps.append(ConnectCheckStep(f'GET {PIPELINES_ROUTE}', True, summary))


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
    print(f'   Endpoint:   {result.endpoint_name}')
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
