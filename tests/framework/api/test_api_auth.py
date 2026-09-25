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

import json
from pathlib import Path
from typing import List
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from finiex_auth.bearer_auth import build_bearer_dependency
from finiex_auth.grant_auth import build_grant_dependency
from finiex_auth.route_walk import assert_no_identity_route_is_ungated
from finiex_auth.token_registry import TokenRegistry

from python.api import api_auth_setup
from python.api.api_app import ROUTER_SURFACES, create_app
from python.api.api_auth_setup import ApiAuthBundle, _api_exception
from python.api.api_contract import CONTRACT_HEADER
from python.configuration.api_auth.api_token_manager import ApiTokenManager
from python.configuration.credential_guard import is_tracked_credential
from python.framework.exceptions.api_errors import ApiConfigurationError
from python.framework.types.api.api_identity_types import ApiConsumerIdentity
from python.framework.types.config_types.api_auth_config_types import (
    AccountKind,
    ApiAccount,
    ConsumerToken,
)

# Routes that must exist for the walk to mean anything. Named rather than only swept: a router
# dropping out of the app would otherwise leave the walk green while its surface went dark.
_REQUIRED_ROUTES = (
    ('/api/v1/brokers/{broker}/symbols', 'get'),
    ('/api/v1/brokers/{broker}/symbols/{symbol}/bars', 'get'),
    ('/api/v1/deployments/{deployment_id}', 'get'),
    ('/api/v1/reports/runs/{run_id}/trade-history', 'get'),
    ('/api/v1/sweeps/{sweep_id}', 'get'),
)

# The account every token in this file acts for. Every token names one (#551); which one is
# irrelevant to the gate — a grant is held by the CONSUMER, never by its account.
_ACCOUNT = 'analyst'
_ACCOUNT_ENTRY = {_ACCOUNT: {'kind': 'person', 'display_name': 'Analyst'}}
_ACCOUNT_MODEL = ApiAccount(account_id=_ACCOUNT, **_ACCOUNT_ENTRY[_ACCOUNT])

# The cascade's two directories and the two files that live in them, spelled as the loader
# joins them — relative to the working directory, which the file-based tests move into tmp.
_WORKSPACE = 'user_configs/credentials'
_TRACKED = 'configs/credentials'
_TOKENS = 'inbound/consumer_tokens.json'
_ACCOUNTS = 'inbound/accounts.json'


def _token(token: str, grants: List[str], note: str) -> ConsumerToken:
    """
    A consumer token acting for the test account.

    Args:
        token: The plaintext bearer value
        grants: What it may reach
        note: Also the consumer's name in `_client`

    Returns:
        The token model
    """
    return ConsumerToken(token=token, grants=grants, account=_ACCOUNT, note=note)


def _entry(token: str, active: bool) -> dict:
    """
    One raw consumer entry, as the credentials file holds it.

    Args:
        token: The plaintext bearer value
        active: The kill switch

    Returns:
        The entry mapping
    """
    return {'token': token, 'grants': ['*'], 'account': _ACCOUNT, 'active': active}


def _write(root: Path, relative: str, section: str, entries: dict) -> None:
    """
    Write one credentials file into a throwaway tree.

    Args:
        root: The tree's root, used as the working directory
        relative: Path below the root
        section: The top-level key, `consumers` or `accounts`
        entries: The section's entries
    """
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({section: entries}))


def _client(*consumers: ConsumerToken, require_auth: bool = True,
            bind_accounts: bool = True) -> TestClient:
    """
    Build an app whose registry holds exactly the given consumers.

    Args:
        consumers: Token models, keyed by their note for readability
        require_auth: Whether gating is enforced; default on, because that is the state every
            other test in this file is about
        bind_accounts: Whether each live consumer is bound to the test account, as the real
            boot binds it; off only to build the state the boot refuses

    Returns:
        A test client over that app
    """
    tokens = {token.note: token for token in consumers}
    registry = TokenRegistry(tokens, source='test')
    identities = {
        name: ApiConsumerIdentity(consumer=name, account=_ACCOUNT_MODEL,
                                  grants=list(token.grants), note=token.note)
        for name, token in tokens.items() if token.active
    } if bind_accounts else {}
    bundle = ApiAuthBundle(
        registry=registry,
        bearer=(build_bearer_dependency(registry, None, _api_exception)
                if require_auth else None),
        grant=build_grant_dependency(registry, _api_exception),
        boot_line='test',
        identities=identities,
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
        client = _client(_token(token='t-full', grants=['*'], note='full'),
                         require_auth=False)
        assert client.get('/api/v1/brokers').status_code == 200
        assert client.get('/api/v1/reports/runs').status_code == 200

    def test_the_same_registry_gates_when_the_switch_is_on(self):
        client = _client(_token(token='t-full', grants=['*'], note='full'),
                         require_auth=True)
        assert client.get('/api/v1/brokers').status_code == 401

    def test_the_boot_line_tells_the_two_states_apart(self):
        # From a request they look identical — only the boot line says which one you are in.
        manager = ApiTokenManager()
        registry = TokenRegistry(
            {'c': _token(token='t', grants=['bars:*'], note='c')}, source='test')
        assert 'NOT enforced' in manager.describe(registry, require_auth=False)
        assert 'ENFORCED' in manager.describe(registry, require_auth=True)


class TestATokenIsRequired:

    def test_no_header_is_refused(self):
        client = _client(_token(token='t-full', grants=['*'], note='full'))
        assert client.get('/api/v1/brokers').status_code == 401

    def test_the_refusal_says_which_scheme_to_retry_with(self):
        # Without WWW-Authenticate a client cannot tell a dead credential from a transport
        # fault — the same conversion §43 forbids one layer down.
        client = _client(_token(token='t-full', grants=['*'], note='full'))
        response = client.get('/api/v1/brokers')
        assert response.headers.get('www-authenticate') == 'Bearer'
        assert response.json()['error'] == 'unauthenticated'

    def test_an_unknown_token_is_refused(self):
        client = _client(_token(token='t-full', grants=['*'], note='full'))
        assert client.get('/api/v1/brokers',
                          headers=_headers('not-a-token')).status_code == 401

    def test_health_stays_open(self):
        # A consumer must be able to tell "the server is down" from "my token is dead"
        # without holding a credential.
        client = _client(_token(token='t-full', grants=['*'], note='full'))
        assert client.get('/api/v1/health').status_code == 200


class TestHoldingATokenIsNotHoldingAGrant:
    """The half that is declared per router, and therefore the half that can be forgotten."""

    def test_no_identity_route_is_ungated(self):
        empty = _token(token='t-nothing', grants=[], note='nothing')
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
        partner = _token(token='t-bars', grants=['bars:kraken_spot', 'brokers:*'],
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
        partner = _token(token='t-bars', grants=['bars:kraken_spot', 'brokers:*'],
                         note='partner')
        client = _client(partner)
        response = client.get('/api/v1/reports/runs/any_run/trade-history',
                              headers=_headers('t-bars'))
        assert response.status_code == 403
        assert response.json()['error'] == 'forbidden'

    def test_a_grant_for_another_broker_does_not_carry(self):
        partner = _token(token='t-mt5', grants=['bars:mt5'], note='partner')
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
        client = _client(_token(token='t-bars', grants=['bars:*', 'brokers:*'],
                                note='partner'))
        assert client.get('/api/v1/reports/runs',
                          headers=_headers('t-bars')).status_code == 403

    def test_the_sweep_list_is_refused_without_a_sweeps_grant(self):
        client = _client(_token(token='t-bars', grants=['bars:*', 'brokers:*'],
                                note='partner'))
        assert client.get('/api/v1/sweeps', headers=_headers('t-bars')).status_code == 403

    def test_the_deployment_list_is_refused_without_a_deployments_grant(self):
        client = _client(_token(token='t-bars', grants=['bars:*', 'brokers:*'],
                                note='partner'))
        assert client.get('/api/v1/deployments',
                          headers=_headers('t-bars')).status_code == 403

    def test_a_deployments_grant_reaches_the_deployment_list(self):
        client = _client(_token(token='t-dep', grants=['deployments:*'], note='ops'))
        assert client.get('/api/v1/deployments',
                          headers=_headers('t-dep')).status_code == 200

    def test_a_reports_grant_reaches_the_run_index(self):
        client = _client(_token(token='t-rep', grants=['reports:*', 'sweeps:*'],
                                note='analyst'))
        assert client.get('/api/v1/reports/runs',
                          headers=_headers('t-rep')).status_code == 200

    def test_a_symbol_list_is_refused_without_a_bars_grant(self):
        # The mirror image, so the floor is shown to cut both ways rather than to be one
        # surface's special case.
        client = _client(_token(token='t-rep', grants=['reports:*', 'sweeps:*'],
                                note='analyst'))
        assert client.get('/api/v1/brokers/kraken_spot/symbols',
                          headers=_headers('t-rep')).status_code == 403


class TestTheAppLevelRoutesAreADecision:
    """
    `/timeframes` and `/brokers` are declared on the app, not on a router.

    A dependency given at `include_router` never reaches them, so they would have stayed open
    by ACCIDENT. Both states are now chosen, and pinned here so neither drifts back:

    - `/timeframes` is OPEN beside `/health`. It is the app's own static configuration, not
      data about a venue or a run, and it is none of the surfaces a grant can name.
      Gating it would make a market-data grant the precondition for a list that reveals
      nothing about market data.
    - `/brokers` requires a token. It names which venues this installation carries, which is
      a fact about the installation. It takes no grant, because it has no path parameter for
      one to be about.
    """

    def test_timeframes_is_open(self):
        client = _client(_token(token='t-any', grants=['bars:*'], note='c'))
        assert client.get('/api/v1/timeframes').status_code == 200

    def test_the_broker_list_requires_a_token(self):
        client = _client(_token(token='t-any', grants=['bars:*'], note='c'))
        assert client.get('/api/v1/brokers').status_code == 401

    def test_any_grant_reaches_the_broker_list(self):
        client = _client(_token(token='t-any', grants=['bars:*'], note='c'))
        assert client.get('/api/v1/brokers', headers=_headers('t-any')).status_code == 200

    def test_a_token_holding_NOTHING_still_reaches_the_broker_list(self):
        # The test that says "deliberate". A route behind the bearer check with no declared
        # surface looks exactly like a router whose Security(..., scopes=[...]) was forgotten
        # — which is the failure the walk exists to catch — so without this assertion nobody
        # can tell the choice from the omission. Named on the package author's suggestion,
        # and on record with them: if the walk ever grows to cover collection routes, it
        # takes an explicit list of routes that are token-only by design, and this is ours.
        client = _client(_token(token='t-none', grants=[], note='c'))
        assert client.get('/api/v1/brokers', headers=_headers('t-none')).status_code == 200


class TestTheCallerRouteSaysWhoIsCalling:
    """
    `/caller` answers what a caller could not find out any other way: which client its token
    authenticates as, which account that client acts for, and what it may reach (#551).

    Token-only like `/brokers` and for the same reason: it is about the caller, it has no path
    parameter for a grant to name, and a grant needed to ask what one holds would be circular.
    `enforced` keeps apart the two states that look alike from a request — with gating off
    nothing verifies a token, so the answer names nobody even for a caller that sent a valid one.
    """

    def test_a_valid_token_is_named_with_its_account_and_grants(self):
        client = _client(_token(token='t-view', grants=['bars:*', 'reports:*'],
                                note='viewer'))
        response = client.get('/api/v1/caller', headers=_headers('t-view'))

        assert response.status_code == 200
        assert response.json() == {
            'enforced': True,
            'client': 'viewer',
            'account': _ACCOUNT,
            'account_kind': AccountKind.PERSON.value,
            'display_name': 'Analyst',
            'grants': ['bars:*', 'reports:*'],
            'note': 'viewer',
        }

    def test_a_wrong_token_is_refused_with_the_scheme_to_retry_with(self):
        client = _client(_token(token='t-view', grants=['*'], note='viewer'))
        response = client.get('/api/v1/caller', headers=_headers('not-a-token'))

        assert response.status_code == 401
        assert response.headers.get('www-authenticate') == 'Bearer'
        assert response.json()['error'] == 'unauthenticated'

    def test_no_header_is_refused(self):
        client = _client(_token(token='t-view', grants=['*'], note='viewer'))
        assert client.get('/api/v1/caller').status_code == 401

    def test_a_token_holding_NOTHING_still_learns_that_it_holds_nothing(self):
        # Token-only by design, like /brokers. A grant floor here would refuse exactly the
        # caller that most needs the answer.
        client = _client(_token(token='t-none', grants=[], note='c'))
        response = client.get('/api/v1/caller', headers=_headers('t-none'))

        assert response.status_code == 200
        assert response.json()['grants'] == []

    def test_with_gating_off_it_names_nobody_even_for_a_valid_token(self):
        # A 200 in the rollout window is not the token being accepted, and the page must be
        # able to see that rather than read a name that nothing verified.
        client = _client(_token(token='t-view', grants=['*'], note='viewer'),
                         require_auth=False)
        response = client.get('/api/v1/caller', headers=_headers('t-view'))

        assert response.status_code == 200
        assert response.json() == {
            'enforced': False, 'client': None, 'account': None, 'account_kind': None,
            'display_name': None, 'grants': [], 'note': None,
        }

    def test_with_no_consumer_configured_it_answers_unenforced(self):
        # The suite runs with config isolation, so the tracked placeholder answers and its
        # entries are all switched off — the real scaffold state, built by the real boot.
        response = TestClient(create_app()).get('/api/v1/caller')

        assert response.status_code == 200
        assert response.json()['enforced'] is False
        assert response.json()['client'] is None

    def test_the_real_boot_reaches_the_route(self, tmp_path, monkeypatch):
        # From files on disk to the answer, so a bundle that stopped carrying the bound
        # identities would fail here and not only in the model-level test.
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv('FINIEX_CONFIG_ISOLATION', '0')
        _write(tmp_path, f'{_WORKSPACE}/{_TOKENS}', 'consumers',
               {'viewer': {'token': 't-real', 'grants': ['bars:*'], 'account': _ACCOUNT,
                           'note': 'dev proxy'}})
        _write(tmp_path, f'{_WORKSPACE}/{_ACCOUNTS}', 'accounts', _ACCOUNT_ENTRY)

        class _Gated:
            def get_api_require_auth(self) -> bool:
                return True

        monkeypatch.setattr(api_auth_setup, 'AppConfigManager', _Gated)
        bundle = api_auth_setup.setup_api_auth()
        monkeypatch.undo()

        with patch('python.api.api_app.setup_api_auth', return_value=bundle):
            client = TestClient(create_app())
        body = client.get('/api/v1/caller', headers=_headers('t-real')).json()

        assert (body['client'], body['account'], body['grants'], body['note']) == (
            'viewer', _ACCOUNT, ['bars:*'], 'dev proxy')

    def test_a_verified_consumer_without_an_account_is_a_defect_not_a_stranger(self):
        # The boot refuses a live token with no account, so this state is reachable only by a
        # defect on this side. Answering it as an anonymous caller would hide the defect.
        client = _client(_token(token='t-view', grants=['*'], note='viewer'),
                         bind_accounts=False)
        response = client.get('/api/v1/caller', headers=_headers('t-view'))

        assert response.status_code == 500
        assert response.json()['error'] == 'identity_unbound'

    def test_it_is_not_a_grant_surface(self):
        # A surface would be a grant a token needs in order to ask what it holds. The
        # vocabulary-vs-mount-table test stays the authority on the set; this pins the route
        # outside both of its halves.
        assert 'caller' not in ConsumerToken.GRANT_SURFACES
        routed = {route.path for router, _ in ROUTER_SURFACES for route in router.routes}
        assert not any(path.endswith('/caller') for path in routed)

    def test_the_route_arrived_with_contract_4(self):
        # The caller route moved the contract to 4. Asserted as a floor rather than an equality, so the
        # next bump does not turn this red for a reason that has nothing to do with the route —
        # what it catches is the bump being forgotten.
        client = _client(_token(token='t-view', grants=['*'], note='viewer'))
        response = client.get('/api/v1/caller', headers=_headers('t-view'))

        assert int(response.headers[CONTRACT_HEADER]) >= 4


class TestTheSchemaSurfaceIsOffWhereItCannotBeGuarded:
    """
    `/openapi.json`, `/docs` and `/redoc` are FastAPI's own, mounted at the app root.

    Three properties put them outside every guard this application has, and each one alone
    would be enough to make them a decision rather than a default. They sit outside
    `/api/v1`, so a reverse proxy scoped to the versioned prefix does not reach them.
    Authentication is attached per ROUTER, which cannot cover a route the framework mounts
    itself. And the walk that proves no identity route is ungated filters on a path
    parameter, so a parameterless root route is outside it BY CONSTRUCTION — it reported
    nothing because it never looked.

    Gating them was considered and does not work: Swagger UI fetches the schema from the
    browser with no bearer, so a gated `/docs` is a broken `/docs`. They are therefore tied
    to the auth posture — present while nobody is configured, gone the moment somebody is.
    """

    def test_the_schema_is_gone_once_a_consumer_exists(self):
        client = _client(_token(token='t-any', grants=['bars:*'], note='c'))

        for path in ('/openapi.json', '/docs', '/redoc'):
            assert client.get(path).status_code == 404, path

    def test_a_token_does_not_bring_it_back(self):
        """Not gated, absent — so holding a credential is not a way in either."""
        client = _client(_token(token='t-any', grants=['bars:*'], note='c'))

        assert client.get('/openapi.json', headers=_headers('t-any')).status_code == 404

    def test_it_is_there_while_no_consumer_is_configured(self):
        """
        The scaffold state keeps the development convenience it is there for.

        `require_auth=False` is what models it: with nobody configured the real
        `setup_api_auth` builds no bearer dependency at all, which is the state this
        switch reads.
        """
        client = _client(require_auth=False)

        assert client.get('/openapi.json').status_code == 200
        assert client.get('/docs').status_code == 200

    def test_the_data_routes_are_unaffected_either_way(self):
        """The schema going away must not be a way to break the API it describes."""
        client = _client(_token(token='t-any', grants=['bars:*'], note='c'))

        assert client.get('/api/v1/timeframes').status_code == 200


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
        client = _client(_token(token='t-any', grants=['*'], note='c'))
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
        client = _client(_token(token='t-any', grants=['*'], note='c'))
        response = client.get('/api/v1/brokers',
                              headers={'Origin': 'http://localhost:5173'},
                              )
        exposed = response.headers.get('access-control-expose-headers', '')
        assert 'WWW-Authenticate' in exposed
        assert 'Retry-After' in exposed


class TestTheTokenFileIsRefusedWhenItIsTheTrackedOne:
    """
    Against real files at their real relative paths, in a throwaway tree.

    The first version of these tests handed the loader a path string, and that string was the
    FLAT layout (`configs/credentials/consumer_tokens.json`). The file had meanwhile moved into
    `inbound/`, the check still tested only the immediate parent directory, and it returned
    False for the real path — so the refusal never fired while its tests stayed green.
    """

    def test_a_live_token_in_the_committed_file_refuses_the_boot(self, tmp_path, monkeypatch):
        # The expensive half of §29 is not a placeholder reaching production — it is a real
        # key reaching the repository, and that file is committed.
        monkeypatch.chdir(tmp_path)
        _write(tmp_path, f'{_TRACKED}/{_TOKENS}', 'consumers',
               {'someone': _entry(token='real', active=True)})
        _write(tmp_path, f'{_TRACKED}/{_ACCOUNTS}', 'accounts', _ACCOUNT_ENTRY)

        with pytest.raises(ApiConfigurationError, match='TRACKED') as raised:
            ApiTokenManager().build_registry()
        assert f'{_TRACKED}/{_TOKENS}' in str(raised.value)

    def test_the_tracked_refusal_comes_before_the_missing_account_one(self, tmp_path,
                                                                        monkeypatch):
        # Every later refusal tells the operator to edit the file that answered. For the
        # committed file the only right edit is to move the key OUT, so a live key that also
        # lacks an account must hear that first — not be told to add an account to it.
        monkeypatch.chdir(tmp_path)
        entry = _entry(token='committed-secret', active=True)
        del entry['account']
        _write(tmp_path, f'{_TRACKED}/{_TOKENS}', 'consumers', {'someone': entry})

        with pytest.raises(ApiConfigurationError, match='TRACKED') as raised:
            ApiTokenManager().build_registry()
        message = str(raised.value)
        assert 'someone' in message
        assert 'name no account' not in message
        # The consumer is named, the token never (§29).
        assert 'committed-secret' not in message

    def test_the_tracked_refusal_comes_before_the_parse(self, tmp_path, monkeypatch):
        # A live entry that would not even parse is still a key in the repository.
        monkeypatch.chdir(tmp_path)
        _write(tmp_path, f'{_TRACKED}/{_TOKENS}', 'consumers',
               {'someone': {**_entry(token='real', active=True), 'grants': ['report:x']}})

        with pytest.raises(ApiConfigurationError, match='TRACKED'):
            ApiTokenManager().build_registry()

    def test_the_tracked_refusal_comes_before_the_account_binding(self, tmp_path, monkeypatch):
        # No accounts file at all: the binding would refuse the unknown account first.
        monkeypatch.chdir(tmp_path)
        _write(tmp_path, f'{_TRACKED}/{_TOKENS}', 'consumers',
               {'someone': _entry(token='real', active=True)})

        with pytest.raises(ApiConfigurationError, match='TRACKED'):
            ApiTokenManager().build_registry()

    @pytest.mark.parametrize('flag', [True, 'true', 1, 'maybe'])
    def test_an_entry_not_declared_off_counts_as_live(self, tmp_path, monkeypatch, flag):
        # Read with the token model's own coercion, and a value that coercion cannot read
        # counts as live: a committed file must not be where a maybe-live key sits.
        monkeypatch.chdir(tmp_path)
        _write(tmp_path, f'{_TRACKED}/{_TOKENS}', 'consumers',
               {'someone': {**_entry(token='real', active=True), 'active': flag}})

        with pytest.raises(ApiConfigurationError, match='TRACKED'):
            ApiTokenManager().build_registry()

    @pytest.mark.parametrize('flag', [False, 'false', 0])
    def test_an_entry_declared_off_in_any_spelling_the_model_reads_passes(self, tmp_path,
                                                                           monkeypatch, flag):
        # The raw read and the parsed one agree on every value both accept.
        monkeypatch.chdir(tmp_path)
        _write(tmp_path, f'{_TRACKED}/{_TOKENS}', 'consumers',
               {'example': {**_entry(token='', active=False), 'active': flag}})

        assert ApiTokenManager().build_registry().is_empty()

    def test_the_real_tracked_path_is_recognised_at_its_real_depth(self):
        assert is_tracked_credential(Path(f'{_TRACKED}/{_TOKENS}'))
        assert is_tracked_credential(Path(f'/app/{_TRACKED}/{_ACCOUNTS}'))
        assert not is_tracked_credential(Path(f'{_WORKSPACE}/{_TOKENS}'))

    def test_the_path_the_loader_really_answers_from_is_recognised(self):
        # Not a path this file spells: the one the loader reports for the committed
        # placeholder, read under the suite's config isolation. A check that stopped matching
        # the real layout would fail here even if every spelled path above still matched.
        source = ApiTokenManager().build_registry().source()

        assert source.endswith(_TOKENS)
        assert is_tracked_credential(Path(source))

    def test_the_workspace_file_is_NOT_mistaken_for_the_tracked_one(self, tmp_path, monkeypatch):
        # The trap this walked into: 'configs/credentials' is a substring of
        # 'user_configs/credentials', so a substring test refuses the REAL file and every live
        # run fails. credential_guard.py documents exactly this and compares part by part; this
        # module did not, until it refused its own workspace tokens.
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv('FINIEX_CONFIG_ISOLATION', '0')
        _write(tmp_path, f'{_WORKSPACE}/{_TOKENS}', 'consumers',
               {'someone': _entry(token='real', active=True)})
        _write(tmp_path, f'{_WORKSPACE}/{_ACCOUNTS}', 'accounts', _ACCOUNT_ENTRY)

        assert not ApiTokenManager().build_registry().is_empty()

    def test_an_inactive_entry_in_the_committed_file_is_fine(self, tmp_path, monkeypatch):
        # Which is what lets the tracked placeholder carry readable examples at all.
        monkeypatch.chdir(tmp_path)
        _write(tmp_path, f'{_TRACKED}/{_TOKENS}', 'consumers',
               {'example': _entry(token='', active=False)})

        assert ApiTokenManager().build_registry().is_empty()


class TestTheSurfaceVocabularyIsClosed:

    def test_an_unknown_surface_fails_when_the_token_is_parsed(self):
        # At config-parse time, not as a denial at request time that nobody can explain.
        with pytest.raises(ValueError, match='known surface'):
            ConsumerToken(token='t', grants=['report:foo'], account=_ACCOUNT, note='typo')

    def test_every_surface_is_a_mounted_router_and_every_router_has_one(self):
        # The vocabulary and the mount table are the two halves of one declaration (§49), and
        # holding them to the same set is what makes either safe to extend: a router mounted
        # under a surface no token can name is authenticated but ungrantable, and a surface no
        # router serves is a grant that silently means nothing.
        assert set(ConsumerToken.GRANT_SURFACES) == {s for _, s in ROUTER_SURFACES}


class TestTheRegistryNeverHoldsAToken:

    def test_only_digests_are_stored(self):
        token = _token(token='super-secret', grants=['*'], note='c')
        registry = TokenRegistry({'c': token}, source='test')
        # The IN-MEMORY registry, not the file: the credentials file holds the plaintext, so a
        # leaked file is a leaked credential. What this protects is a memory dump, a log line or
        # a support ticket carrying the registry object — none of those leak the token.
        assert 'super-secret' not in repr(registry.__dict__)
        assert registry.verify('super-secret') == 'c'

    def test_the_boot_line_names_consumers_and_never_tokens(self):
        manager = ApiTokenManager()
        registry = TokenRegistry(
            {'c': _token(token='super-secret', grants=['bars:*'], note='c')},
            source='test')
        line = manager.describe(registry, require_auth=True)
        assert 'c' in line
        assert 'super-secret' not in line
