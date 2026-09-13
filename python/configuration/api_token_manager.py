"""
FiniexTestingIDE - API Token Manager

Where this project's API consumer tokens come from. The shared package decides what a token
MEANS; this decides where ours live, which is deliberately not the package's business.
"""

import json
import secrets
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from finiex_auth.token_registry import TokenRegistry
from pydantic import ValidationError

from python.framework.exceptions.api_errors import ApiConfigurationError
from python.framework.types.config_types.api_auth_config_types import ConsumerToken

# The cascade every credential in this project uses, most specific first.
_CREDENTIAL_DIRS = ('user_configs/credentials', 'configs/credentials')
_CREDENTIALS_FILE = 'api_tokens.json'

# The directory whose copy is COMMITTED, as the two path parts that identify it. Compared PART
# BY PART and never as a substring: 'configs/credentials' is a substring of
# 'user_configs/credentials', so a substring test refuses the REAL file too — the same trap
# credential_guard.py documents, and one this module walked straight into.
_TRACKED_PARENT = 'configs'
_CREDENTIALS_DIR_NAME = 'credentials'

# 32 bytes of urandom, url-safe. Well past guessing, and short enough to paste by hand.
_TOKEN_BYTES = 32


def _is_tracked(path: Path) -> bool:
    """
    Whether a credentials path is the COMMITTED one rather than the workspace override.

    Part by part from the end, never a substring: the tracked directory's name is contained in
    the override's, so a substring test answers true for both and refuses the real file.

    Args:
        path: The credentials file that answered

    Returns:
        True when it came from the tracked directory
    """
    parts = path.parts
    return (len(parts) >= 3
            and parts[-2] == _CREDENTIALS_DIR_NAME
            and parts[-3] == _TRACKED_PARENT)


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

    def build_registry(self) -> TokenRegistry:
        """
        Load the configured consumers and build the registry the API gates on.

        Returns:
            Registry of active consumers; empty when nothing is configured
        """
        raw, source = self._read()
        self._source = source
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

        # A committed file that produces a LIVE consumer is the expensive half of §29 — not a
        # placeholder reaching production, but a real key reaching the repository. An empty
        # result from the same file is the ordinary scaffold state and passes.
        if source and _is_tracked(Path(source)) and not registry.is_empty():
            raise ApiConfigurationError(
                f'A live API token answered from the TRACKED file {source}. That file is '
                f'committed — move the real tokens to user_configs/credentials/'
                f'{self._credentials_file}, which takes precedence and is not tracked.'
            )
        return registry

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
        line = (f'API authentication: {gate} · {len(registry.names())} consumer(s) '
                f'[{", ".join(registry.names())}] from {self._source}')
        return line + (f' · switched off: {", ".join(inactive)}' if inactive else '')

    def render_mint(self, consumer: str, grants: List[str], note: str) -> str:
        """
        Mint one consumer token and render what the operator has to do with it.

        The plaintext is shown ONCE and stored nowhere: the registry keeps only a SHA-256
        digest, so a configuration file that leaks is not a leaked credential. That is the
        same reason a provider shows an API key once and never again — and it means a lost
        token is re-minted, never recovered.

        The grants are validated here rather than on first use, so a typo is a refusal now
        instead of a denial the holder cannot explain later.

        Args:
            consumer: The consumer's name, as it will appear in logs and grants
            grants: What it may reach, as `<surface>:<name>` entries
            note: One line recording who holds it

        Returns:
            The block to show the operator — the token, and the entry to paste
        """
        token = secrets.token_urlsafe(_TOKEN_BYTES)
        # Constructing it IS the validation: an unknown surface fails right here.
        entry = ConsumerToken(token=token, grants=grants, active=True, note=note)
        block = json.dumps(
            {consumer: entry.model_dump()}, indent=2,
            ensure_ascii=False)[1:-1].strip().rstrip(',')
        target = Path(_CREDENTIAL_DIRS[0]) / self._credentials_file
        return (
            f'Consumer: {consumer}\n'
            f'Grants:   {", ".join(grants)}\n\n'
            f'TOKEN (shown once, stored nowhere — copy it now):\n\n'
            f'    {token}\n\n'
            f'Add this under "consumers" in {target}:\n\n'
            f'{block}\n'
        )

    def _read(self) -> Tuple[Dict, Optional[str]]:
        """
        Read the first credentials file the cascade offers.

        Returns:
            The consumers mapping and the path that answered, or ({}, None)
        """
        for directory in _CREDENTIAL_DIRS:
            path = Path(directory) / self._credentials_file
            if not path.exists():
                continue
            try:
                with open(path, 'r') as handle:
                    return json.load(handle).get('consumers', {}), str(path)
            except json.JSONDecodeError as error:
                raise ApiConfigurationError(
                    f'Invalid JSON in {path}\n{error}\nFix the syntax or remove the file.'
                )
        return {}, None
