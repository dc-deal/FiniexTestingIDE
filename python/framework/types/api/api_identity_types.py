"""
FiniexTestingIDE - API Identity Types

Who a consumer token is, resolved once at boot (#551): the CLIENT a token authenticates as, the
ACCOUNT it acts for, and what it may reach.
"""

from dataclasses import dataclass
from typing import List

from python.framework.types.config_types.api_auth_config_types import ApiAccount


@dataclass
class ApiConsumerIdentity:
    """
    One live consumer as the API knows it after boot.

    The grants are carried as a LIST because the shared registry renders them only as one joined
    string, and a response that re-split that string would be parsing our own display text.

    Args:
        consumer: The name the token authenticates as — the client
        account: The account it acts for — the person or service on whose behalf
        grants: What it may reach, as `<surface>:<name>` entries
        note: The token's note — who holds it
    """
    consumer: str
    account: ApiAccount
    grants: List[str]
    note: str
