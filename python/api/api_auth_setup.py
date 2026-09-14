"""
FiniexTestingIDE — API Authentication Setup

Builds the token registry and the two dependencies the routers mount, and hands them back as
one bundle. Construction lives here so `create_app` stays a list of what is mounted rather
than how it was assembled.
"""

from dataclasses import dataclass
from typing import Callable, Dict, Optional

from finiex_auth.bearer_auth import build_bearer_dependency
from finiex_auth.grant_auth import build_grant_dependency
from finiex_auth.rate_limiter import RateLimiter
from finiex_auth.token_registry import TokenRegistry

from python.configuration.api_token_manager import ApiTokenManager
from python.configuration.app_config_manager import AppConfigManager
from python.framework.exceptions.api_errors import ApiException

# Failed attempts per client per minute. It bounds credential guessing, not traffic: a valid
# call is never counted. Deliberately generous — a human retrying a stale token must not be
# locked out while a script hammering the door is.
_FAILED_ATTEMPTS_PER_MINUTE = 30


@dataclass
class ApiAuthBundle:
    """
    The wired authentication layer, handed to `create_app`.

    A bundle of live collaborators rather than data, so it lives beside its builder (§6).
    """

    registry: TokenRegistry
    bearer: Optional[Callable]
    grant: Callable
    boot_line: str


def _api_exception(status_code: int, error: str, detail: str,
                   headers: Optional[Dict[str, str]] = None) -> Exception:
    """
    Turn the package's failure into this project's error contract.

    The package decides THAT a request fails and why; what it looks like on the wire is ours,
    and ours is `ApiException` rather than a raw `HTTPException`. `headers` is carried through
    — a 401 without `WWW-Authenticate` is an answer a conforming client cannot act on.

    Args:
        status_code: 401, 403 or 429
        error: A code from the package's closed vocabulary
        detail: The human-readable sentence
        headers: Headers this answer needs, or None

    Returns:
        The exception the API layer raises
    """
    return ApiException(status_code=status_code, error=error, detail=detail, headers=headers)


def setup_api_auth() -> ApiAuthBundle:
    """
    Load the configured consumers and build the dependencies the routers mount.

    TWO conditions, and they are separate on purpose. The bearer dependency is built only when
    `api.require_auth` is on AND a consumer is configured; otherwise the grant dependency finds
    no consumer on the request and returns early, so behaviour is exactly what it was before.

    Collapsing them would make the first token written into the credentials file gate every
    route in the same instant — and the rollout needs the window in between, because a consumer
    has to hold its token before it can start sending the header. It is a state to pass
    through, not to stay in.

    Returns:
        The registry, the dependencies, and one line saying which state we are in
    """
    manager = ApiTokenManager()
    registry = manager.build_registry()
    require_auth = AppConfigManager().get_api_require_auth()
    boot_line = manager.describe(registry, require_auth)

    bearer = None
    if require_auth and not registry.is_empty():
        # The limiter keys on the CONNECTION, not on a header a caller supplies. Behind our
        # loopback publish that is the docker gateway, so every host process shares one key —
        # harmless, because only FAILING requests consume the bucket. A caller varying a
        # header on each guess cannot escape it.
        # Generous on purpose: a person retrying a stale token must not lock themselves out
        # while a script hammering the door still does. Only 401s count; a wrong GRANT is a
        # configuration error a client cannot fix by retrying, and is never throttled.
        limiter = RateLimiter(per_minute=_FAILED_ATTEMPTS_PER_MINUTE)
        bearer = build_bearer_dependency(registry, limiter, _api_exception)

    return ApiAuthBundle(
        registry=registry,
        bearer=bearer,
        grant=build_grant_dependency(registry, _api_exception),
        boot_line=boot_line,
    )
