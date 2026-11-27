"""
FiniexTestingIDE - Package Service Resolution
Whether the import may fetch from a package service, decided once for every service (#120): its
switch first, then its address. Each peer's configuration manager hands its own block and its
active endpoint here, so the data collector and the RAG engine cannot come to differ in what
"switched on" means.
"""

from python.framework.types.config_types.package_service_config_types import (
    ActivePackageSource,
    PackageFetchConfig,
)
from python.framework.types.import_types.import_progress_types import ImportSection
from python.framework.types.import_types.package_types import (
    PackageServiceResolution,
    PackageServiceState,
)


def resolve_package_service(
    package_service: str,
    delivers: ImportSection,
    config: PackageFetchConfig,
    endpoint_name: str,
    base_url: str,
    credentials_file: str,
) -> PackageServiceResolution:
    """
    Resolve one package service from its block and its active endpoint.

    Args:
        package_service: The service's name, e.g. 'collector'
        delivers: The archive its packages feed
        config: Its block — the switch and the ladder
        endpoint_name: The active endpoint's name in its file
        base_url: The active endpoint's address, empty when none is configured
        credentials_file: The credential file's configured name — never the token

    Returns:
        The resolution: ON with the source to fetch from, or the state that keeps it off
    """
    if not config.enabled:
        state = PackageServiceState.SWITCHED_OFF
    elif not base_url:
        state = PackageServiceState.NO_ADDRESS
    else:
        state = PackageServiceState.ON
    source = ActivePackageSource(
        package_service=package_service,
        delivers=delivers,
        endpoint_name=endpoint_name,
        base_url=base_url.rstrip('/'),
        credentials_file=credentials_file,
        connection=config.connection) if state is PackageServiceState.ON else None
    return PackageServiceResolution(
        package_service=package_service,
        delivers=delivers,
        state=state,
        endpoint_name=endpoint_name,
        address_set=bool(base_url),
        credentials_file=credentials_file,
        source=source)
