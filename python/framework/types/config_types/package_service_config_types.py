"""
FiniexTestingIDE - Package Service Configuration Types
What every package service the import fetches from declares (#120): whether it is switched on and
the ladder of its reads — and the service as the import resolved it, its address and credential
file together. The data collector serves tick packages, the RAG engine signal packages; each
declares its block in its own peer's file.
"""

from dataclasses import dataclass

from pydantic import Field

from python.framework.types.config_types.connection_policy_config_types import ConnectionPolicy
from python.framework.types.config_types.strict_config_model import StrictConfigModel
from python.framework.types.connection_types import GiveUpAction
from python.framework.types.import_types.import_progress_types import ImportSection


def package_connection() -> ConnectionPolicy:
    """
    The ladder of a package service's reads: a few attempts, then the import goes on without
    fetching from it. A download of a weekly zip may take a while, hence the long socket timeout.

    Returns:
        The default policy
    """
    return ConnectionPolicy(initial_delay_s=2.0, max_delay_s=30.0, jitter=True,
                            attempt_budget=3, request_timeout_s=60.0,
                            on_give_up=GiveUpAction.DEGRADE)


class PackageFetchConfig(StrictConfigModel):
    """
    A package service's switch and the ladder of its reads.

    Off by default, as every connected source of an installation is: nothing is fetched from a
    service until a workspace copy switches it on, so a clone, the test session and a fresh machine
    never reach one.
    """
    enabled: bool = False
    connection: ConnectionPolicy = Field(default_factory=package_connection)


@dataclass(frozen=True)
class ActivePackageSource:
    """
    A package service an import fetches from, resolved as one unit: address, credential file and
    ladder switch together. The token itself is read at each request, never held here.

    Args:
        package_service: The service's name, e.g. 'collector' or 'rag'
        delivers: The archive its packages feed — ticks or signals
        endpoint_name: Endpoint name from its file's registry (e.g. 'production')
        base_url: Its address, without a trailing slash
        credentials_file: The credential file's configured name, resolved by the cascade
        connection: The ladder of its reads
    """
    package_service: str
    delivers: ImportSection
    endpoint_name: str
    base_url: str
    credentials_file: str
    connection: ConnectionPolicy
