"""
FiniexTestingIDE - API Token Manager

Where this project's API consumer tokens come from. The shared package decides what a token
MEANS; this decides where ours live, which is deliberately not the package's business.

Every token also names the ACCOUNT it acts for (#551), and the boot binds the two: an entry that
names no account refuses the start whether it is switched on or off, and a live token whose account
does not exist or is switched off refuses it too.
"""

import json
import secrets
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from finiex_auth.token_registry import TokenRegistry
from pydantic import TypeAdapter, ValidationError

from python.configuration.api_auth.api_account_manager import ApiAccountManager
from python.configuration.credential_guard import is_tracked_credential
from python.configuration.api_auth.inbound_credentials_cascade import (
    WORKSPACE_CREDENTIALS_DIR,
    read_inbound_section,
)
from python.framework.exceptions.api_errors import ApiConfigurationError
from python.framework.types.api.api_identity_types import ApiConsumerIdentity
from python.framework.types.config_types.api_auth_config_types import ApiAccount, ConsumerToken

_CREDENTIALS_FILE = 'inbound/consumer_tokens.json'
_CONSUMERS_SECTION = 'consumers'

# 32 bytes of urandom, url-safe. Well past guessing, and short enough to paste by hand.
_TOKEN_BYTES = 32

# The command that prints an account entry — named in every refusal that needs one.
_ACCOUNT_COMMAND = ('python python/cli/api_token_cli.py account --id <id> '
                    "--kind person|service --display-name '<name>'")

# How an entry's `active` is read before the entry is parsed — the same lax coercion the token
# model applies, so the raw read and the parsed one cannot disagree about a value both accept.
_ACTIVE_FLAG = TypeAdapter(bool)


def _live_entry_names(raw: Dict[str, Dict]) -> List[str]:
    """
    The raw entries that would authenticate, read before anything is parsed.

    An entry is live unless its `active` is declared false. A value the coercion cannot read
    counts as LIVE: the question is asked of a committed file, and a committed file must not be
    where a maybe-live key sits.

    Args:
        raw: Consumer name → entry, as the credentials file holds it

    Returns:
        The names of the live entries, sorted
    """
    live: List[str] = []
    for name, entry in raw.items():
        try:
            active = _ACTIVE_FLAG.validate_python(entry.get('active', True))
        except ValidationError:
            active = True
        if active:
            live.append(name)
    return sorted(live)


class ApiTokenManager:
    """
    Reads the API consumer tokens and hands the shared registry a plain mapping.

    An EMPTY registry is a legitimate state, not a failure: it is the scaffold step of the
    rollout, in which the package is wired but nothing is gated and the existing consumer is
    unaffected. What is NOT legitimate is a live token answering from the tracked file, and
    those two are only distinguishable if the answering FILE is carried alongside the value —
    the same reason the producer credential carries its source.
    """

    def __init__(self, credentials_file: str = _CREDENTIALS_FILE):
        """
        Args:
            credentials_file: File name inside the credentials directories
        """
        self._credentials_file = credentials_file
        self._source: Optional[str] = None
        self._account_manager = ApiAccountManager()
        self._identities: Dict[str, ApiConsumerIdentity] = {}

    def build_registry(self) -> TokenRegistry:
        """
        Load the configured consumers and build the registry the API gates on.

        Every entry must name an account, switched off or not; only a LIVE entry's account must
        exist and be active. The resolved pairs are kept and handed out by `get_identities()`.

        Returns:
            Registry of active consumers; empty when nothing is configured
        """
        raw, source = self._read()
        self._source = source

        # A committed file that holds a LIVE consumer is the expensive half of §29 — not a
        # placeholder reaching production, but a real key reaching the repository. Checked FIRST
        # and on the raw entries: every later refusal tells the operator to edit the file that
        # answered, and for this file the only right edit is to move the key out of it. A file
        # whose entries are all switched off is the ordinary scaffold state and passes.
        if source and is_tracked_credential(Path(source)):
            live = _live_entry_names(raw)
            if live:
                raise ApiConfigurationError(
                    f'A live API token answered from the TRACKED file {source} '
                    f'({", ".join(live)}). That file is committed — move the real tokens to '
                    f'{WORKSPACE_CREDENTIALS_DIR}/{self._credentials_file}, which takes '
                    f'precedence and is not tracked.'
                )

        # All of them at once: the file that needs this is usually one written before accounts
        # existed, so every entry lacks one, and a refusal per restart would be one per entry.
        unbound = sorted(name for name, entry in raw.items() if not entry.get('account'))
        if unbound:
            raise ApiConfigurationError(self._no_account_message(unbound, source))
        tokens: Dict[str, ConsumerToken] = {}
        for name, entry in raw.items():
            try:
                tokens[name] = ConsumerToken(**entry)
            except ValidationError as error:
                raise ApiConfigurationError(
                    f'API consumer {name!r} in {source} is not a valid token entry '
                    f'(value not shown).\n{error}'
                )

        registry = TokenRegistry(tokens, source=source or 'none')
        self._identities = self._bind_accounts(tokens, source)
        return registry

    def get_identities(self) -> Dict[str, ApiConsumerIdentity]:
        """
        Each live consumer with the account it acts for, as the last `build_registry` bound them.

        Returns:
            Consumer name → identity; empty before a build, or when no consumer is live
        """
        return dict(self._identities)

    def describe(self, registry: TokenRegistry, require_auth: bool) -> str:
        """
        One operator-readable line about the token state — never a token (§29).

        It names BOTH conditions, because a token that exists while gating is off and a token
        that does not exist look identical from a request. Only the boot line tells them apart.

        Args:
            registry: The registry just built
            require_auth: Whether gating is switched on

        Returns:
            What was loaded, from where, and whether anything is gated
        """
        if registry.is_empty():
            return (f'API authentication: NO consumers configured (source: '
                    f'{self._source or "no file"}) — routes are ungated')
        inactive = registry.inactive_names()
        gate = ('ENFORCED' if require_auth else
                'NOT enforced (api.require_auth is off — tokens exist, nothing is gated)')
        # Each consumer beside the account it acts for, once the accounts are bound.
        named = [f'{name} ({self._identities[name].account.account_id})'
                 if name in self._identities else name
                 for name in registry.names()]
        line = (f'API authentication: {gate} · {len(registry.names())} consumer(s) '
                f'[{", ".join(named)}] from {self._source}')
        return line + (f' · switched off: {", ".join(inactive)}' if inactive else '')

    def render_mint(self, consumer: str, grants: List[str], account: str, note: str) -> str:
        """
        Mint one consumer token and render what the operator has to do with it.

        The plaintext is shown ONCE and never written by this command — but the entry the
        operator pastes carries it, so the credentials file does hold it. Only the in-memory
        registry reduces it to a SHA-256 digest. A lost token is re-minted rather than looked up.

        The grants and the account are validated here rather than on first use, so a typo is a
        refusal now instead of a boot the operator cannot explain later. The account is also
        checked against the accounts file whenever that file can be read.

        Args:
            consumer: The consumer's name, as it will appear in logs and grants
            grants: What it may reach, as `<surface>:<name>` entries
            account: The account the token acts for
            note: One line recording who holds it

        Returns:
            The block to show the operator — the token, and the entry to paste
        """
        token = secrets.token_urlsafe(_TOKEN_BYTES)
        # Constructing it IS the validation: an unknown surface or a malformed account fails
        # right here.
        entry = ConsumerToken(token=token, grants=grants, account=account, active=True,
                              note=note)
        account_line = self._account_line_for_mint(account)
        block = json.dumps(
            {consumer: entry.model_dump()}, indent=2,
            ensure_ascii=False)[1:-1].strip().rstrip(',')
        target = Path(WORKSPACE_CREDENTIALS_DIR) / self._credentials_file
        return (
            f'Consumer: {consumer}\n'
            f'Account:  {account_line}\n'
            f'Grants:   {", ".join(grants)}\n\n'
            f'TOKEN (shown once — copy it now; the entry below carries it):\n\n'
            f'    {token}\n\n'
            f'Add this under "consumers" in {target}:\n\n'
            f'{block}\n'
        )

    def _account_line_for_mint(self, account: str) -> str:
        """
        Check a mint's account against the accounts file, where that file can be read.

        A known, active account passes; an unknown or switched-off one refuses, because the
        boot would refuse the token anyway. A file that is absent or unreadable is not a refusal
        — the mint writes nothing — but the line says what the boot will need.

        Args:
            account: The account the token will act for

        Returns:
            The account line for the mint block
        """
        try:
            accounts = self._account_manager.load_accounts()
        except ApiConfigurationError as error:
            return (f'{account} — NOT CHECKED: the accounts file could not be read, and the '
                    f'API refuses to boot until it can.\n          {error}')
        source = self._account_manager.get_source()
        if source is None:
            return (f'{account} — no accounts file exists yet. Create '
                    f'{self._account_manager.workspace_path()} with this account before the '
                    f'restart:\n          {_ACCOUNT_COMMAND}')
        known = accounts.get(account)
        if known is None:
            raise ApiConfigurationError(
                f'The account {account!r} does not exist in {source}. Create it first — the '
                f'API refuses to boot a token whose account is unknown:\n    {_ACCOUNT_COMMAND}'
            )
        if not known.active:
            raise ApiConfigurationError(
                f'The account {account!r} in {source} is switched off (active: false). The API '
                f'refuses to boot a live token bound to it — re-activate it first.'
            )
        return f'{account} ({known.kind.value} · {known.display_name})'

    def _bind_accounts(self, tokens: Dict[str, ConsumerToken],
                       source: Optional[str]) -> Dict[str, ApiConsumerIdentity]:
        """
        Resolve the account every LIVE token acts for, refusing one whose account does not exist
        or is switched off.

        A switched-off token is not bound: it cannot authenticate, so an example in a template
        file may name an account that does not exist — it still has to NAME one, which
        `build_registry` has already checked.

        Args:
            tokens: Every parsed entry, active or not
            source: The token file that answered

        Returns:
            Consumer name → identity, for the live consumers
        """
        accounts: Dict[str, ApiAccount] = self._account_manager.load_accounts()
        accounts_source = self._account_manager.get_source() or 'no accounts file'
        identities: Dict[str, ApiConsumerIdentity] = {}
        unknown: List[str] = []
        switched_off: List[str] = []
        for name, token in sorted(tokens.items()):
            if not token.active:
                continue
            account = accounts.get(token.account)
            if account is None:
                unknown.append(f'{name} → {token.account}')
                continue
            if not account.active:
                switched_off.append(f'{name} → {token.account}')
                continue
            identities[name] = ApiConsumerIdentity(
                consumer=name, account=account, grants=list(token.grants), note=token.note)

        if unknown:
            raise ApiConfigurationError(
                f'API consumers in {source} act for accounts that do not exist '
                f'(accounts read from: {accounts_source}):\n'
                f'    {", ".join(unknown)}\n'
                f'    Add each to {self._account_manager.workspace_path()} — this prints the '
                f'entry to paste:\n'
                f'        {_ACCOUNT_COMMAND}\n'
                f'    then restart the API server; both files are read at boot.'
            )
        if switched_off:
            raise ApiConfigurationError(
                f'API consumers in {source} act for accounts that are switched off '
                f'(active: false) in {accounts_source}:\n'
                f'    {", ".join(switched_off)}\n'
                f'    A token must not outlive the account it acts for: switch the token off as '
                f'well, or re-activate the account, then restart the API server.'
            )
        return identities

    def _no_account_message(self, names: List[str], source: Optional[str]) -> str:
        """
        The refusal for token entries that name no account, with the remedy spelled out.

        Args:
            names: The consumers without an account
            source: The token file that answered

        Returns:
            The message
        """
        return (
            f'API consumer(s) in {source} name no account: {", ".join(names)}.\n'
            f'    Every token acts for an ACCOUNT: the token says which client is calling, the '
            f'account on whose\n'
            f'    behalf. Without one, anything such a client starts could not say who it was '
            f'started for.\n'
            f'    1. Print one account entry per person or service and paste it under '
            f'"accounts" in\n'
            f'       {self._account_manager.workspace_path()}:\n'
            f'           {_ACCOUNT_COMMAND}\n'
            f'    2. Add  "account": "<id>"  to each of those entries in {source}.\n'
            f'    3. Restart the API server — both files are read at boot.'
        )

    def _read(self) -> Tuple[Dict, Optional[str]]:
        """
        Read the first credentials file the cascade offers.

        Returns:
            The consumers mapping and the path that answered, or ({}, None)
        """
        return read_inbound_section(self._credentials_file, _CONSUMERS_SECTION)
