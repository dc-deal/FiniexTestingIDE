"""
FiniexTestingIDE - Data Collector Configuration Types
How the import reaches the data collector's package service (#120): the service's address beside
the credential that opens it, and the connection ladder of its reads.
"""

from dataclasses import dataclass
from typing import Dict

from pydantic import Field

from python.framework.types.config_types.connection_policy_config_types import ConnectionPolicy
from python.framework.types.config_types.strict_config_model import StrictConfigModel
from python.framework.types.connection_types import GiveUpAction


class DataCollectorEndpoint(StrictConfigModel):
    """
    One reachable package service: where it is and which credential opens it. The two switch
    together — a token against the wrong address is a 401 that reads like a broken service.
    """
    base_url: str = ''
    credentials_file: str = 'peers/collector_credentials.json'


def _package_connection() -> ConnectionPolicy:
    """
    The ladder of the package service's reads: a few attempts, then the import goes on without
    fetching. A download of a weekly zip may take a while, hence the long socket timeout.

    Returns:
        The default policy
    """
    return ConnectionPolicy(initial_delay_s=2.0, max_delay_s=30.0, jitter=True,
                            attempt_budget=3, request_timeout_s=60.0,
                            on_give_up=GiveUpAction.DEGRADE)


class TickPackagesConfig(StrictConfigModel):
    """
    The registered package service endpoints, which one is active, and the ladder of its reads.

    The tracked production address is empty, so nothing is fetched until a workspace copy names
    it: a clone, the test session and a fresh machine never reach the collector.
    """
    active: str = 'production'
    endpoints: Dict[str, DataCollectorEndpoint] = Field(
        default_factory=lambda: {'production': DataCollectorEndpoint()})
    connection: ConnectionPolicy = Field(default_factory=_package_connection)

    def get_active_endpoint(self) -> DataCollectorEndpoint:
        """
        The endpoint named by `active`.

        Returns:
            Its configuration
        """
        endpoint = self.endpoints.get(self.active)
        if endpoint is None:
            known = ', '.join(sorted(self.endpoints)) or '(none registered)'
            raise ValueError(
                f"data_collector_config.json: packages.active is '{self.active}', which is not "
                f'a registered endpoint. Known endpoints: {known}')
        return endpoint


class DataCollectorConfig(StrictConfigModel):
    """
    The data collector as a peer we call: today its tick package service only.
    """
    packages: TickPackagesConfig = Field(default_factory=TickPackagesConfig)


@dataclass(frozen=True)
class ActivePackageSource:
    """
    The package service an import fetches from, resolved as one unit: address, credential file
    and ladder switch together. The token itself is read at each request, never held here.

    Args:
        name: Endpoint name from the registry (e.g. 'production')
        base_url: Its address, without a trailing slash
        credentials_file: The credential file's configured name, resolved by the cascade
        connection: The ladder of its reads
    """
    name: str
    base_url: str
    credentials_file: str
    connection: ConnectionPolicy
