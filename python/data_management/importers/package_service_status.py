"""
FiniexTestingIDE - Package Service Status
What `data_index_cli.py package-sources` shows (#120): every package service an import can fetch
from — what it delivers, whether it is switched on, its endpoint and address, the credential as the
cascade answers it — and, when asked to check, what each switched-on service's list holds. The
figures are derived here; the console renderer only formats them.
"""

from typing import Callable, List

from python.data_management.importers.package_client import PackageClient, read_package_credential
from python.framework.types.config_types.package_service_config_types import ActivePackageSource
from python.framework.types.import_types.package_types import (
    PackageServiceResolution,
    PackageServiceState,
    PackageServiceStatus,
    PackageState,
)


def build_package_service_status(
    services: List[PackageServiceResolution],
    check: bool,
    client_factory: Callable[[ActivePackageSource], PackageClient] = PackageClient,
) -> List[PackageServiceStatus]:
    """
    One status row per package service.

    Args:
        services: Every package service as its configuration resolves it
        check: Read each switched-on service's list, through its ladder
        client_factory: Builds a service's client; the package client when omitted

    Returns:
        The rows, in the services' order
    """
    rows: List[PackageServiceStatus] = []
    for service in services:
        credential = read_package_credential(service.credentials_file).describe_source()
        base = dict(package_service=service.package_service, delivers=service.delivers,
                    state=service.state, endpoint_name=service.endpoint_name,
                    address_set=service.address_set, credential=credential)
        if not check:
            rows.append(PackageServiceStatus(**base))
            continue
        if service.state is not PackageServiceState.ON:
            rows.append(PackageServiceStatus(
                **base, check_note=f'not read — {service.describe_state()}'))
            continue
        listings = client_factory(service.source).list_packages()
        if listings is None:
            rows.append(PackageServiceStatus(
                **base, check_note='not read — the service did not answer'))
            continue
        rows.append(PackageServiceStatus(
            **base,
            waiting=sum(1 for listing in listings if listing.state is PackageState.READY),
            acknowledged=sum(1 for listing in listings
                             if listing.state is PackageState.DELIVERED),
            newest_package=listings[-1].package_id if listings else None))
    return rows
