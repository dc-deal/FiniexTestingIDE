"""
FiniexTestingIDE - API Authentication Tests

The API answered anyone who could reach it. CORS is not access control — it is a browser
mechanism, and the fifteen report routes serve the artifacts of every run.

Two halves are tested, and the second is the one that gets forgotten: that a token is required
at all, and that holding a token is not the same as being entitled to what it asks for.
Authentication is inherited from the router; authorization is declared per router, so a router
mounted without its scopes is authenticated but ungated and looks identical to one that is not.
Only a walk over the surface can tell them apart, which is why the shared package ships that
walk and this suite calls it.
"""

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from finiex_auth.bearer_auth import build_bearer_dependency
from finiex_auth.grant_auth import build_grant_dependency
from finiex_auth.route_walk import assert_no_identity_route_is_ungated
from finiex_auth.token_registry import TokenRegistry

from python.api.api_app import create_app
from python.api.api_auth_setup import ApiAuthBundle, _api_exception
from python.configuration.api_token_manager import ApiTokenManager
from python.framework.exceptions.api_errors import ApiConfigurationError
from python.framework.types.config_types.api_auth_config_types import ConsumerToken

# Routes that must exist for the walk to mean anything. Named rather than only swept: a router
# dropping out of the app would otherwise leave the walk green while its surface went dark.
_REQUIRED_ROUTES = (
    ('/api/v1/brokers/{broker}/symbols', 'get'),
    ('/api/v1/brokers/{broker}/symbols/{symbol}/bars', 'get'),
    ('/api/v1/reports/runs/{run_id}/trade-history', 'get'),
    ('/api/v1/sweeps/{sweep_id}', 'get'),
)


def _client(*consumers: ConsumerToken, require_auth: bool = True) -> TestClient:
    """
    Build an app whose registry holds exactly the given consumers.

    Args:
        consumers: Token models, keyed by their note for readability
        require_auth: Whether gating is enforced; default on, because that is the state every
            other test in this file is about

    Returns:
        A test client over that app
    """
    tokens = {token.note: token for token in consumers}
    registry = TokenRegistry(tokens, source='test')
    bundle = ApiAuthBundle(
        registry=registry,
        bearer=(build_bearer_dependency(registry, None, _api_exception)
                if require_auth else None),
        grant=build_grant_dependency(registry, _api_exception),
        boot_line='test',
    )
    with patch('python.api.api_app.setup_api_auth', return_value=bundle):
        return TestClient(create_app())


def _headers(token: str) -> dict:
    return {'Authorization': f'Bearer {token}'}


class TestTheScaffoldStateChangesNothing:
    """
    Step one of the rollout: the package is wired and nothing is gated.

    It has to be byte-identical, or the step is not reversible and the existing consumer
    finds out before anyone told it.
    """

    def test_without_consumers_every_route_still_answers(self):
        # The suite runs with config isolation, so the tracked placeholder answers — and its
        # entries are all switched off, which is what makes it produce an empty registry.
        client = TestClient(create_app())
        assert client.get('/api/v1/health').status_code == 200
        assert client.get('/api/v1/timeframes').status_code == 200
        assert client.get('/api/v1/brokers').status_code == 200


class TestGatingAndTokensAreTwoSwitches:
    """
    A configured token and an enforced gate are different states, and collapsing them broke
    the agreed rollout.

    A consumer has to HOLD its token before it can start sending the header, so there must be
    a window in which tokens exist and sending one is still optional. The browser client asked
    for exactly that window in as many words. Without the flag, the first token written into
    the credentials file would gate every route in the same instant.
    """

    def test_a_token_may_exist_while_nothing_is_gated(self):
        client = _client(ConsumerToken(token='t-full', grants=['*'], note='full'),
                         require_auth=False)
        assert client.get('/api/v1/brokers').status_code == 200
        assert client.get('/api/v1/reports/runs').status_code == 200

    def test_the_same_registry_gates_when_the_switch_is_on(self):
        client = _client(ConsumerToken(token='t-full', grants=['*'], note='full'),
                         require_auth=True)
        assert client.get('/api/v1/brokers').status_code == 401

    def test_the_boot_line_tells_the_two_states_apart(self):
        # From a request they look identical — only the boot line says which one you are in.
        manager = ApiTokenManager()
        registry = TokenRegistry(
            {'c': ConsumerToken(token='t', grants=['bars:*'], note='c')}, source='test')
        assert 'NOT enforced' in manager.describe(registry, require_auth=False)
        assert 'ENFORCED' in manager.describe(registry, require_auth=True)


class TestATokenIsRequired:

    def test_no_header_is_refused(self):
        client = _client(ConsumerToken(token='t-full', grants=['*'], note='full'))
        assert client.get('/api/v1/brokers').status_code == 401

    def test_the_refusal_says_which_scheme_to_retry_with(self):
        # Without WWW-Authenticate a client cannot tell a dead credential from a transport
        # fault — the same conversion §43 forbids one layer down.
        client = _client(ConsumerToken(token='t-full', grants=['*'], note='full'))
        response = client.get('/api/v1/brokers')
        assert response.headers.get('www-authenticate') == 'Bearer'
        assert response.json()['error'] == 'unauthenticated'

    def test_an_unknown_token_is_refused(self):
        client = _client(ConsumerToken(token='t-full', grants=['*'], note='full'))
        assert client.get('/api/v1/brokers',
                          headers=_headers('not-a-token')).status_code == 401

    def test_health_stays_open(self):
        # A consumer must be able to tell "the server is down" from "my token is dead"
        # without holding a credential.
        client = _client(ConsumerToken(token='t-full', grants=['*'], note='full'))
        assert client.get('/api/v1/health').status_code == 200


class TestHoldingATokenIsNotHoldingAGrant:
    """The half that is declared per router, and therefore the half that can be forgotten."""

    def test_no_identity_route_is_ungated(self):
        empty = ConsumerToken(token='t-nothing', grants=[], note='nothing')
        client = _client(empty)
        walked = assert_no_identity_route_is_ungated(
            client.app, client, _headers('t-nothing'),
            required=_REQUIRED_ROUTES,
            # A run id is typed as a string, a symbol likewise — but any value does, because
            # the grant check refuses before the name is ever resolved.
            fill=lambda name: 'x',
        )
        assert len(walked) >= len(_REQUIRED_ROUTES)

    def test_a_market_data_token_reaches_its_broker(self):
        partner = ConsumerToken(token='t-bars', grants=['bars:kraken_spot', 'brokers:*'],
                                note='partner')
        client = _client(partner)
        # Not 403: the grant holds. Whether the data exists is a different question and a
        # different status.
        assert client.get('/api/v1/brokers/kraken_spot/symbols',
                          headers=_headers('t-bars')).status_code != 403

    def test_the_same_token_is_refused_on_a_report(self):
        # This is the boundary that matters: the report routes serve the artifacts of every
        # run, and a market-data partner has no business there. It stops being a promise and
        # becomes something the code enforces.
        partner = ConsumerToken(token='t-bars', grants=['bars:kraken_spot', 'brokers:*'],
                                note='partner')
        client = _client(partner)
        response = client.get('/api/v1/reports/runs/any_run/trade-history',
                              headers=_headers('t-bars'))
        assert response.status_code == 403
        assert response.json()['error'] == 'forbidden'

    def test_a_grant_for_another_broker_does_not_carry(self):
        partner = ConsumerToken(token='t-mt5', grants=['bars:mt5'], note='partner')
        client = _client(partner)
        assert client.get('/api/v1/brokers/kraken_spot/symbols/BTCUSD/bars',
                          headers=_headers('t-mt5')).status_code == 403


class TestACollectionRouteIsGatedToo:
    """
    The gap the walk cannot see, and it leaked.

    A grant is `<surface>:<name>` where the name is the route's FIRST path parameter, so a
    COLLECTION route has nothing to be about and the grant check returned early. Measured
    2026-09-13 against a token holding only `bars:*` and `brokers:*`: `/reports/runs` answered
    200 with 116 runs — the full index, naming every live run of a private strategy — and
    `/sweeps` answered 200 with 14. Both identity routes beside them were correctly refused.

    The package closed it with a floor: a collection route requires at least one grant on its
    router's surface. These tests exist because the walk only calls routes containing `{`, so
    a collection route has to be named by hand or nothing looks at it.
    """

    def test_the_run_index_is_refused_without_a_reports_grant(self):
        client = _client(ConsumerToken(token='t-bars', grants=['bars:*', 'brokers:*'],
                                       note='partner'))
        assert client.get('/api/v1/reports/runs',
                          headers=_headers('t-bars')).status_code == 403

    def test_the_sweep_list_is_refused_without_a_sweeps_grant(self):
        client = _client(ConsumerToken(token='t-bars', grants=['bars:*', 'brokers:*'],
                                       note='partner'))
        assert client.get('/api/v1/sweeps', headers=_headers('t-bars')).status_code == 403

    def test_a_reports_grant_reaches_the_run_index(self):
        client = _client(ConsumerToken(token='t-rep', grants=['reports:*', 'sweeps:*'],
                                       note='analyst'))
        assert client.get('/api/v1/reports/runs',
                          headers=_headers('t-rep')).status_code == 200

    def test_a_symbol_list_is_refused_without_a_bars_grant(self):
        # The mirror image, so the floor is shown to cut both ways rather than to be one
        # surface's special case.
        client = _client(ConsumerToken(token='t-rep', grants=['reports:*', 'sweeps:*'],
                                       note='analyst'))
        assert client.get('/api/v1/brokers/kraken_spot/symbols',
                          headers=_headers('t-rep')).status_code == 403


class TestTheAppLevelRoutesAreADecision:
    """
    `/timeframes` and `/brokers` are declared on the app, not on a router.

    A dependency given at `include_router` never reaches them, so they would have stayed open
    by ACCIDENT. Both states are now chosen, and pinned here so neither drifts back:

    - `/timeframes` is OPEN beside `/health`. It is the app's own static configuration, not
      data about a venue or a run, and it is none of the four surfaces a grant can name.
      Gating it would make a market-data grant the precondition for a list that reveals
      nothing about market data.
    - `/brokers` requires a token. It names which venues this installation carries, which is
      a fact about the installation. It takes no grant, because it has no path parameter for
      one to be about.
    """

    def test_timeframes_is_open(self):
        client = _client(ConsumerToken(token='t-any', grants=['bars:*'], note='c'))
        assert client.get('/api/v1/timeframes').status_code == 200

    def test_the_broker_list_requires_a_token(self):
        client = _client(ConsumerToken(token='t-any', grants=['bars:*'], note='c'))
        assert client.get('/api/v1/brokers').status_code == 401

    def test_any_grant_reaches_the_broker_list(self):
        client = _client(ConsumerToken(token='t-any', grants=['bars:*'], note='c'))
        assert client.get('/api/v1/brokers', headers=_headers('t-any')).status_code == 200

    def test_a_token_holding_NOTHING_still_reaches_the_broker_list(self):
        # The test that says "deliberate". A route behind the bearer check with no declared
        # surface looks exactly like a router whose Security(..., scopes=[...]) was forgotten
        # — which is the failure the walk exists to catch — so without this assertion nobody
        # can tell the choice from the omission. Named on the package author's suggestion,
        # and on record with them: if the walk ever grows to cover collection routes, it
        # takes an explicit list of routes that are token-only by design, and this is ours.
        client = _client(ConsumerToken(token='t-none', grants=[], note='c'))
        assert client.get('/api/v1/brokers', headers=_headers('t-none')).status_code == 200


class TestTheCorsPreflightIsNeverGated:
    """
    A browser sends OPTIONS before a cross-origin request carrying a custom header, and that
    preflight carries NO Authorization — by specification.

    If the bearer check ran on it, the preflight would answer 401 and the real request would
    never be made. In a browser that surfaces as a CORS error with no status, which is among
    the least diagnosable failures there is. Neither of this API's other consumers can see it:
    one is server-side and sends no preflight, the other is this server. And the browser
    client will not see it in development either, because its dev proxy makes every request
    same-origin — it would appear for the first time in a deployment.
    """

    def test_a_preflight_without_a_token_is_not_refused(self):
        client = _client(ConsumerToken(token='t-any', grants=['*'], note='c'))
        response = client.options(
            '/api/v1/reports/runs',
            headers={'Origin': 'http://localhost:5173',
                     'Access-Control-Request-Method': 'GET',
                     'Access-Control-Request-Headers': 'authorization'})
        assert response.status_code != 401
        assert response.headers.get('access-control-allow-origin')

    def test_the_headers_a_browser_needs_are_exposed(self):
        # A browser hides every response header that is not CORS-safelisted, so a 401's
        # WWW-Authenticate and a 429's Retry-After are invisible unless the server lists them.
        client = _client(ConsumerToken(token='t-any', grants=['*'], note='c'))
        response = client.get('/api/v1/brokers',
                              headers={'Origin': 'http://localhost:5173'},
                              )
        exposed = response.headers.get('access-control-expose-headers', '')
        assert 'WWW-Authenticate' in exposed
        assert 'Retry-After' in exposed


class TestTheTokenFileIsRefusedWhenItIsTheTrackedOne:

    def test_a_live_token_in_the_committed_file_refuses_the_boot(self, monkeypatch):
        # The expensive half of §29 is not a placeholder reaching production — it is a real
        # key reaching the repository, and that file is committed.
        manager = ApiTokenManager()

        def _tracked(_self) -> tuple:
            return ({'someone': {'token': 'real', 'grants': ['*'], 'active': True}},
                    'configs/credentials/api_tokens.json')

        monkeypatch.setattr(ApiTokenManager, '_read', _tracked)
        with pytest.raises(ApiConfigurationError, match='TRACKED'):
            manager.build_registry()

    def test_the_workspace_file_is_NOT_mistaken_for_the_tracked_one(self, monkeypatch):
        # The trap this walked into: 'configs/credentials' is a substring of
        # 'user_configs/credentials', so a substring test refuses the REAL file and every live
        # run fails. credential_guard.py documents exactly this and compares part by part; this
        # module did not, until it refused its own workspace tokens.
        manager = ApiTokenManager()

        def _workspace(_self) -> tuple:
            return ({'someone': {'token': 'real', 'grants': ['*'], 'active': True}},
                    'user_configs/credentials/api_tokens.json')

        monkeypatch.setattr(ApiTokenManager, '_read', _workspace)
        assert not manager.build_registry().is_empty()

    def test_an_inactive_entry_in_the_committed_file_is_fine(self, monkeypatch):
        # Which is what lets the tracked placeholder carry readable examples at all.
        manager = ApiTokenManager()

        def _tracked(_self) -> tuple:
            return ({'example': {'token': '', 'grants': ['*'], 'active': False}},
                    'configs/credentials/api_tokens.json')

        monkeypatch.setattr(ApiTokenManager, '_read', _tracked)
        assert manager.build_registry().is_empty()


class TestTheSurfaceVocabularyIsClosed:

    def test_an_unknown_surface_fails_when_the_token_is_parsed(self):
        # At config-parse time, not as a denial at request time that nobody can explain.
        with pytest.raises(ValueError, match='known surface'):
            ConsumerToken(token='t', grants=['report:foo'], note='typo')

    def test_the_four_surfaces_are_the_four_routers(self):
        assert ConsumerToken.GRANT_SURFACES == ('bars', 'brokers', 'reports', 'sweeps')


class TestTheRegistryNeverHoldsAToken:

    def test_only_digests_are_stored(self):
        token = ConsumerToken(token='super-secret', grants=['*'], note='c')
        registry = TokenRegistry({'c': token}, source='test')
        # A configuration file that leaks, a memory dump, or a support ticket carrying this
        # object is then not a leaked credential.
        assert 'super-secret' not in repr(registry.__dict__)
        assert registry.verify('super-secret') == 'c'

    def test_the_boot_line_names_consumers_and_never_tokens(self):
        manager = ApiTokenManager()
        registry = TokenRegistry(
            {'c': ConsumerToken(token='super-secret', grants=['bars:*'], note='c')},
            source='test')
        line = manager.describe(registry, require_auth=True)
        assert 'c' in line
        assert 'super-secret' not in line
