"""
FiniexTestingIDE - API Account Manager

Where the accounts API consumer tokens act for come from (#551). A token identifies a CLIENT —
which program is calling; an account identifies on whose BEHALF — a person, or a machine
consumer such as a sibling service. The two are maintained and revoked together, which is why
the accounts file sits beside the token file and is read through the same cascade.
"""

import json
from pathlib import Path
from typing import Dict, Optional

from pydantic import ValidationError

from python.configuration.api_auth.inbound_credentials_cascade import (
    WORKSPACE_CREDENTIALS_DIR,
    read_inbound_section,
)
from python.framework.exceptions.api_errors import ApiConfigurationError
from python.framework.types.config_types.api_auth_config_types import ApiAccount

ACCOUNTS_FILE = 'inbound/accounts.json'
_ACCOUNTS_SECTION = 'accounts'


class ApiAccountManager:
    """
    Reads the accounts file and renders an entry for the operator to paste.

    No account is a legitimate state — the tracked placeholder holds none — and it only becomes
    a refusal when a LIVE token names an account that is not there. That check belongs to the
    token loader, which is where both halves are in hand.
    """

    def __init__(self, accounts_file: str = ACCOUNTS_FILE):
        """
        Args:
            accounts_file: Path below the credentials directories
        """
        self._accounts_file = accounts_file
        self._source: Optional[str] = None

    def load_accounts(self) -> Dict[str, ApiAccount]:
        """
        Load every configured account, keyed by its id.

        The id is the entry's KEY, the same shape the token file uses for a consumer name. An
        entry may repeat it as `account_id`, but only with the same value.

        Returns:
            Account id → account, active or not; empty when no file answered
        """
        raw, source = read_inbound_section(self._accounts_file, _ACCOUNTS_SECTION)
        self._source = source
        accounts: Dict[str, ApiAccount] = {}
        for key, entry in raw.items():
            data = dict(entry)
            declared = data.pop('account_id', key)
            if declared != key:
                raise ApiConfigurationError(
                    f'The account filed under {key!r} in {source} declares '
                    f'`account_id: {declared!r}`. The key IS the id — drop the field or make '
                    f'the two agree.'
                )
            try:
                accounts[key] = ApiAccount(account_id=key, **data)
            except ValidationError as error:
                raise ApiConfigurationError(
                    f'The account {key!r} in {source} is not a valid account entry.\n{error}'
                )
        return accounts

    def get_source(self) -> Optional[str]:
        """
        The file the last load was answered by.

        Returns:
            Its path, or None when no file existed or nothing was loaded yet
        """
        return self._source

    def workspace_path(self) -> Path:
        """
        Where the operator's real accounts file belongs.

        Returns:
            The workspace path — the one that takes precedence and is not tracked
        """
        return Path(WORKSPACE_CREDENTIALS_DIR) / self._accounts_file

    def render_account(self, account_id: str, kind: str, display_name: str,
                       note: str) -> str:
        """
        Validate one account and render the entry the operator pastes. Writes nothing.

        Refuses an id the file already holds: a pasted duplicate would refuse the next boot
        anyway, and the refusal is cheaper now than after the paste.

        Args:
            account_id: The id tokens will name — same shape as a bot id, never `operator`
            kind: `person` or `service`
            display_name: How a human reads it in a report
            note: One line on who or what this is

        Returns:
            The block to show the operator — the entry, and where it goes
        """
        try:
            account = ApiAccount(account_id=account_id, kind=kind,
                                 display_name=display_name, active=True, note=note)
        except ValidationError as error:
            raise ApiConfigurationError(f'Not a valid account.\n{error}')
        existing = self.load_accounts()
        if account_id in existing:
            raise ApiConfigurationError(
                f'The account {account_id!r} already exists in {self._source}. An account id is '
                f'given out once; choose another, or edit the existing entry.'
            )
        entry = account.model_dump(mode='json', exclude={'account_id'})
        target = self.workspace_path()
        if target.exists():
            where = f'Add this under "accounts" in {target}'
            block = json.dumps({account_id: entry}, indent=2,
                               ensure_ascii=False)[1:-1].strip().rstrip(',')
        else:
            # The first account creates the file, so the block is the whole document.
            where = f'Create {target} with'
            block = json.dumps({_ACCOUNTS_SECTION: {account_id: entry}}, indent=2,
                               ensure_ascii=False)
        return (
            f'Account: {account_id} ({account.kind.value})\n\n'
            f'{where}:\n\n'
            f'{block}\n\n'
            f'Then mint or bind a token with  --account {account_id}  (or add\n'
            f'"account": "{account_id}" to an existing consumer entry) and restart the API\n'
            f'server — both files are read at boot.\n'
        )
