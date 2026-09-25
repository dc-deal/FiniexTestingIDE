"""
FiniexTestingIDE - API Account Tests (#551)

A token identifies a CLIENT — which program is calling. It never said on whose BEHALF, and the
first write surface needs exactly that: a run started through the API has to record a person,
and "whoever held the viewer token" is not one. So every token entry now names an account —
switched off or not — and the boot refuses a live token whose account does not exist or is
switched off.

What these tests pin, beyond the binding itself: the account id obeys the SAME shape rule as a
bot id through one shared predicate rather than a copy; `operator` is reserved for the console
and can never be an account; the two inbound files are read through one cascade that honours
config isolation, so a test run never reads the operator's real tokens; and neither CLI command
writes a file.
"""

import json
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from python.api import api_auth_setup
from python.cli import api_token_cli
from python.configuration.api_auth.api_account_manager import ApiAccountManager
from python.configuration.api_auth.api_token_manager import ApiTokenManager
from python.framework.exceptions.api_errors import ApiConfigurationError
from python.framework.exceptions.persistence_errors import BotIdMalformedError
from python.framework.types.config_types.api_auth_config_types import (
    AccountKind,
    ApiAccount,
    ConsumerToken,
)
from python.framework.types.run_origin_types import OPERATOR_PERSON
from python.framework.utils.declared_id_utils import (
    DECLARED_ID_MAX_LENGTH,
    declared_id_malformed_reason,
)
from python.framework.validators.carry_over_identity_validator import validate_bot_id

_WORKSPACE = 'user_configs/credentials'
_TRACKED = 'configs/credentials'
_TOKENS = 'inbound/consumer_tokens.json'
_ACCOUNTS = 'inbound/accounts.json'

_PERSON = {'kind': 'person', 'display_name': 'Analyst'}
_SERVICE = {'kind': 'service', 'display_name': 'Sibling service'}


def _write(root: Path, relative: str, document: dict) -> Path:
    """
    Write one credentials file into a throwaway tree.

    Args:
        root: The tree's root, used as the working directory
        relative: Path below the root
        document: The whole file

    Returns:
        The written path
    """
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document))
    return path


def _token(account: str = 'analyst', active: bool = True, grants=None) -> dict:
    """
    One raw consumer entry.

    Args:
        account: The account it acts for; empty leaves the key out
        active: The kill switch
        grants: What it may reach; everything by default

    Returns:
        The entry mapping
    """
    entry = {'token': f'secret-{account or "none"}', 'grants': grants or ['*'],
             'active': active, 'note': 'n'}
    if account:
        entry['account'] = account
    return entry


def _tree_files(root: Path) -> dict:
    """
    Every file below a tree with its content, so a test can prove nothing was written.

    Args:
        root: The tree

    Returns:
        Relative path → text
    """
    return {str(p.relative_to(root)): p.read_text() for p in root.rglob('*') if p.is_file()}


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """
    A throwaway tree as the working directory, with config isolation OFF — the state the API
    server and the CLI run in.

    Args:
        tmp_path: pytest's temporary directory
        monkeypatch: pytest's patcher

    Returns:
        The tree's root
    """
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv('FINIEX_CONFIG_ISOLATION', '0')
    return tmp_path


class TestTheAccountIdIsTheBotIdShape:
    """
    One shape rule for every identity a person types, held in ONE predicate.

    The pin is behavioural rather than structural: for every id below, a bot id and an account
    id must agree on whether it is usable. A copied predicate that drifted would split them.
    """

    @pytest.mark.parametrize('value', [
        'a', 'analyst', 'svc-01', 'a' * DECLARED_ID_MAX_LENGTH,
        'a' * (DECLARED_ID_MAX_LENGTH + 1), 'Analyst', 'my_bot', 'a b', 'a.b', 'ä',
    ])
    def test_a_bot_id_and_an_account_id_agree(self, value):
        reason = declared_id_malformed_reason(value)

        try:
            validate_bot_id('p', 'BTCUSD', value)
            bot_ok = True
        except BotIdMalformedError:
            bot_ok = False
        try:
            ApiAccount(account_id=value, **_PERSON)
            account_ok = True
        except ValidationError:
            account_ok = False

        assert bot_ok == account_ok == (reason is None)

    def test_the_refusal_says_by_how_much_it_is_too_long(self):
        with pytest.raises(ValidationError) as raised:
            ApiAccount(account_id='a' * (DECLARED_ID_MAX_LENGTH + 3), **_PERSON)
        assert str(DECLARED_ID_MAX_LENGTH + 3) in str(raised.value)

    def test_an_empty_id_is_refused(self):
        with pytest.raises(ValidationError, match='empty'):
            ApiAccount(account_id='', **_PERSON)


class TestTheOperatorIsNotAnAccount:
    """
    The console operator is a principal of its own. A token acting as it would give an API
    client the one identity that may start real-money runs from a terminal.
    """

    def test_an_account_named_operator_is_refused(self):
        with pytest.raises(ValidationError, match='reserved'):
            ApiAccount(account_id=OPERATOR_PERSON, **_PERSON)

    def test_a_token_naming_operator_is_refused(self):
        with pytest.raises(ValidationError, match='reserved'):
            ConsumerToken(token='t', grants=['*'], account=OPERATOR_PERSON)

    def test_an_accounts_file_naming_operator_refuses_the_parse(self, workspace):
        _write(workspace, f'{_WORKSPACE}/{_ACCOUNTS}', {'accounts': {OPERATOR_PERSON: _PERSON}})
        with pytest.raises(ApiConfigurationError, match='reserved'):
            ApiAccountManager().load_accounts()


class TestTheAccountModel:

    def test_a_person_and_a_service_are_the_two_kinds(self):
        assert {kind.value for kind in AccountKind} == {'person', 'service'}
        assert ApiAccount(account_id='svc', **_SERVICE).kind is AccountKind.SERVICE

    def test_an_unknown_kind_is_refused(self):
        with pytest.raises(ValidationError):
            ApiAccount(account_id='x', kind='robot', display_name='X')

    def test_a_misspelled_field_is_refused_not_dropped(self):
        with pytest.raises(ValidationError):
            ApiAccount(account_id='x', kind='person', display_name='X', actve=False)

    def test_an_account_is_active_by_default(self):
        assert ApiAccount(account_id='x', **_PERSON).active is True


class TestTheAccountsFile:

    def test_no_file_is_no_account_and_not_an_error(self, workspace):
        manager = ApiAccountManager()
        assert manager.load_accounts() == {}
        assert manager.get_source() is None

    def test_the_key_is_the_id(self, workspace):
        _write(workspace, f'{_WORKSPACE}/{_ACCOUNTS}', {'accounts': {'analyst': _PERSON}})
        accounts = ApiAccountManager().load_accounts()
        assert accounts['analyst'].account_id == 'analyst'

    def test_an_entry_repeating_a_different_id_is_refused(self, workspace):
        _write(workspace, f'{_WORKSPACE}/{_ACCOUNTS}',
               {'accounts': {'analyst': {**_PERSON, 'account_id': 'other'}}})
        with pytest.raises(ApiConfigurationError, match='key IS the id'):
            ApiAccountManager().load_accounts()

    def test_an_id_named_twice_is_refused(self, workspace):
        # json.load keeps the LAST of two equal keys, so the first account would vanish.
        path = workspace / _WORKSPACE / _ACCOUNTS
        path.parent.mkdir(parents=True)
        path.write_text('{"accounts": {"analyst": {"kind": "person", "display_name": "A"},'
                        ' "analyst": {"kind": "service", "display_name": "B"}}}')
        with pytest.raises(ApiConfigurationError, match='twice'):
            ApiAccountManager().load_accounts()

    def test_the_workspace_copy_takes_precedence(self, workspace):
        _write(workspace, f'{_TRACKED}/{_ACCOUNTS}', {'accounts': {}})
        _write(workspace, f'{_WORKSPACE}/{_ACCOUNTS}', {'accounts': {'analyst': _PERSON}})
        manager = ApiAccountManager()
        assert 'analyst' in manager.load_accounts()
        assert manager.get_source() == f'{_WORKSPACE}/{_ACCOUNTS}'

    def test_the_tracked_placeholder_holds_no_account(self):
        # The suite runs isolated from /app, so this reads the committed file itself.
        manager = ApiAccountManager()
        assert manager.load_accounts() == {}
        assert manager.get_source() == f'{_TRACKED}/{_ACCOUNTS}'


class TestEveryTokenNamesAnAccount:
    """
    Two rules of different reach. EVERY entry names an account, switched off or not: an entry
    is switched back on by flipping one flag, and that flip must not produce a token acting
    for nobody. Only a LIVE entry's account must exist and be active, which is what lets a
    switched-off example in a template file name an account the file beside it does not hold.
    """

    def test_entries_without_an_account_are_refused_all_at_once(self, workspace):
        # The file that needs this was written before accounts existed, so every entry lacks
        # one — a refusal per restart would cost one restart per consumer.
        _write(workspace, f'{_WORKSPACE}/{_TOKENS}',
               {'consumers': {'viewer': _token(account=''), 'partner': _token(account='')}})
        with pytest.raises(ApiConfigurationError) as raised:
            ApiTokenManager().build_registry()
        message = str(raised.value)
        assert 'partner, viewer' in message
        assert 'api_token_cli.py account' in message
        assert 'Restart the API server' in message

    def test_an_unknown_account_is_refused_and_named(self, workspace):
        _write(workspace, f'{_WORKSPACE}/{_TOKENS}',
               {'consumers': {'viewer': _token(account='ghost')}})
        _write(workspace, f'{_WORKSPACE}/{_ACCOUNTS}', {'accounts': {'analyst': _PERSON}})
        with pytest.raises(ApiConfigurationError) as raised:
            ApiTokenManager().build_registry()
        assert 'viewer → ghost' in str(raised.value)
        assert f'{_WORKSPACE}/{_ACCOUNTS}' in str(raised.value)

    def test_a_switched_off_account_is_refused(self, workspace):
        _write(workspace, f'{_WORKSPACE}/{_TOKENS}',
               {'consumers': {'viewer': _token(account='analyst')}})
        _write(workspace, f'{_WORKSPACE}/{_ACCOUNTS}',
               {'accounts': {'analyst': {**_PERSON, 'active': False}}})
        with pytest.raises(ApiConfigurationError, match='switched off'):
            ApiTokenManager().build_registry()

    def test_a_switched_off_token_may_name_an_account_that_does_not_exist(self, workspace):
        # Which is what lets the tracked placeholder carry readable examples.
        _write(workspace, f'{_WORKSPACE}/{_TOKENS}',
               {'consumers': {'example': _token(account='nobody', active=False)}})
        assert ApiTokenManager().build_registry().is_empty()

    def test_a_switched_off_token_must_still_name_an_account(self, workspace):
        # Off is one flag away from on, and the flip must not produce a token acting for
        # nobody — so the name is required of every entry, not only of a live one.
        _write(workspace, f'{_WORKSPACE}/{_TOKENS}',
               {'consumers': {'example': _token(account='', active=False)}})
        with pytest.raises(ApiConfigurationError, match='name no account') as raised:
            ApiTokenManager().build_registry()
        assert 'example' in str(raised.value)

    def test_a_bound_consumer_carries_its_account_and_its_grants_as_a_list(self, workspace):
        _write(workspace, f'{_WORKSPACE}/{_TOKENS}', {'consumers': {
            'viewer': _token(account='analyst', grants=['bars:*', 'reports:*']),
            'sibling': _token(account='svc', grants=['bars:*']),
            'old': _token(account='gone', active=False),
        }})
        _write(workspace, f'{_WORKSPACE}/{_ACCOUNTS}',
               {'accounts': {'analyst': _PERSON, 'svc': _SERVICE}})
        manager = ApiTokenManager()
        manager.build_registry()
        identities = manager.get_identities()

        assert set(identities) == {'viewer', 'sibling'}
        assert identities['viewer'].account.account_id == 'analyst'
        assert identities['viewer'].grants == ['bars:*', 'reports:*']
        assert identities['sibling'].account.kind is AccountKind.SERVICE

    def test_the_boot_line_names_the_account_beside_the_consumer(self, workspace):
        _write(workspace, f'{_WORKSPACE}/{_TOKENS}',
               {'consumers': {'viewer': _token(account='analyst')}})
        _write(workspace, f'{_WORKSPACE}/{_ACCOUNTS}', {'accounts': {'analyst': _PERSON}})
        manager = ApiTokenManager()
        registry = manager.build_registry()
        line = manager.describe(registry, require_auth=True)
        assert 'viewer (analyst)' in line
        assert 'secret-' not in line

    def test_the_boot_hands_the_identities_to_the_bundle(self, workspace, monkeypatch):
        _write(workspace, f'{_WORKSPACE}/{_TOKENS}',
               {'consumers': {'viewer': _token(account='analyst')}})
        _write(workspace, f'{_WORKSPACE}/{_ACCOUNTS}', {'accounts': {'analyst': _PERSON}})

        class _Gated:
            def get_api_require_auth(self) -> bool:
                return True

        monkeypatch.setattr(api_auth_setup, 'AppConfigManager', _Gated)
        bundle = api_auth_setup.setup_api_auth()
        assert bundle.identities['viewer'].account.account_id == 'analyst'
        assert bundle.bearer is not None


class TestTheTokenLoaderHonoursConfigIsolation:
    """
    Under isolation only the tracked copy answers. Measured before the change: a test run read
    the operator's real token file, so the suite depended on the workspace it ran in and loaded
    a live secret into a process with no business holding one.
    """

    def _both(self, root: Path) -> None:
        _write(root, f'{_WORKSPACE}/{_TOKENS}', {'consumers': {'viewer': _token()}})
        _write(root, f'{_WORKSPACE}/{_ACCOUNTS}', {'accounts': {'analyst': _PERSON}})
        _write(root, f'{_TRACKED}/{_TOKENS}',
               {'consumers': {'example': _token(account='analyst', active=False)}})

    def test_isolation_reads_only_the_tracked_copy(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        monkeypatch.setenv('FINIEX_CONFIG_ISOLATION', '1')
        self._both(tmp_path)
        manager = ApiTokenManager()
        registry = manager.build_registry()
        assert registry.is_empty()
        assert registry.source() == f'{_TRACKED}/{_TOKENS}'

    def test_without_isolation_the_workspace_answers(self, workspace):
        self._both(workspace)
        registry = ApiTokenManager().build_registry()
        assert registry.names() == ['viewer']
        assert registry.source() == f'{_WORKSPACE}/{_TOKENS}'


class TestTheCommandsWriteNothing:
    """Both commands print a block to paste. The files stay the operator's to edit."""

    def test_the_account_block_is_the_whole_file_when_none_exists(self, workspace):
        before = _tree_files(workspace)
        block = ApiAccountManager().render_account('analyst', 'person', 'Analyst', 'viewer user')
        assert f'Create {_WORKSPACE}/{_ACCOUNTS}' in block
        document = json.loads(block[block.index('{'):block.rindex('}') + 1])
        assert document['accounts']['analyst']['kind'] == 'person'
        assert _tree_files(workspace) == before

    def test_the_account_block_is_a_fragment_when_the_file_exists(self, workspace):
        _write(workspace, f'{_WORKSPACE}/{_ACCOUNTS}', {'accounts': {'svc': _SERVICE}})
        block = ApiAccountManager().render_account('analyst', 'person', 'Analyst', '')
        assert 'under "accounts"' in block
        assert '"analyst": {' in block

    def test_an_existing_account_id_is_refused(self, workspace):
        _write(workspace, f'{_WORKSPACE}/{_ACCOUNTS}', {'accounts': {'analyst': _PERSON}})
        with pytest.raises(ApiConfigurationError, match='already exists'):
            ApiAccountManager().render_account('analyst', 'person', 'Analyst', '')

    def test_the_account_block_refuses_the_operator(self, workspace):
        with pytest.raises(ApiConfigurationError, match='reserved'):
            ApiAccountManager().render_account(OPERATOR_PERSON, 'person', 'Me', '')

    def test_a_mint_carries_its_account_and_writes_nothing(self, workspace):
        _write(workspace, f'{_WORKSPACE}/{_ACCOUNTS}', {'accounts': {'analyst': _PERSON}})
        before = _tree_files(workspace)
        block = ApiTokenManager().render_mint('viewer', ['bars:*'], 'analyst', 'dev proxy')
        assert 'Account:  analyst (person · Analyst)' in block
        assert '"account": "analyst"' in block
        assert _tree_files(workspace) == before

    def test_a_mint_for_an_unknown_account_is_refused(self, workspace):
        _write(workspace, f'{_WORKSPACE}/{_ACCOUNTS}', {'accounts': {'analyst': _PERSON}})
        with pytest.raises(ApiConfigurationError, match='does not exist'):
            ApiTokenManager().render_mint('viewer', ['bars:*'], 'ghost', '')

    def test_a_mint_with_no_accounts_file_says_what_the_boot_will_need(self, workspace):
        block = ApiTokenManager().render_mint('viewer', ['bars:*'], 'analyst', '')
        assert 'no accounts file exists yet' in block

    def test_a_mint_for_a_malformed_account_is_refused(self, workspace):
        with pytest.raises(ValidationError):
            ApiTokenManager().render_mint('viewer', ['bars:*'], 'Not_Valid', '')

    def test_mint_without_an_account_is_a_usage_error(self, monkeypatch):
        monkeypatch.setattr(sys, 'argv', ['api_token_cli.py', 'mint', '--consumer', 'viewer',
                                          '--grants', 'bars:*'])
        with pytest.raises(SystemExit) as raised:
            api_token_cli.main()
        assert raised.value.code == 2
