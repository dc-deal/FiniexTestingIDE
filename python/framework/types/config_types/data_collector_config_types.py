"""
FiniexTestingIDE - Data Collector Configuration Types
How the import reaches the data collector's package service (#120): the service's address beside
the credential that opens it; its switch and the connection ladder of its reads are every package
service's (`package_service_config_types.py`).
"""

from typing import Dict

from pydantic import Field

from python.framework.types.config_types.package_service_config_types import PackageFetchConfig
from python.framework.types.config_types.strict_config_model import StrictConfigModel


class DataCollectorEndpoint(StrictConfigModel):
    """
    One reachable package service: where it is and which credential opens it. The two switch
    together — a token against the wrong address is a 401 that reads like a broken service.
    """
    base_url: str = ''
    credentials_file: str = 'peers/collector_credentials.json'


class TickPackagesConfig(PackageFetchConfig):
    """
    The collector's package service: its switch and ladder, the registered endpoints and which one
    is active.

    Switched off and with an empty production address in the tracked file, so nothing is fetched
    until a workspace copy names the address and switches it on: a clone, the test session and a
    fresh machine never reach the collector.
    """
    active: str = 'production'
    endpoints: Dict[str, DataCollectorEndpoint] = Field(
        default_factory=lambda: {'production': DataCollectorEndpoint()})

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
