"""
FiniexTestingIDE - Package Services view (#120)

Every package service an import can fetch from: what it delivers, whether it is switched on, its
endpoint and address, the credential as the cascade answered it — and, after a check, what its list
holds. Formatting only: the rows come from `build_package_service_status()`.
"""

from typing import List

from python.framework.types.import_types.package_types import (
    PackageServiceState,
    PackageServiceStatus,
)

# The words the switch column shows
_STATE_WORDS = {
    PackageServiceState.ON: 'on',
    PackageServiceState.SWITCHED_OFF: 'switched off',
    PackageServiceState.NO_ADDRESS: 'no address',
}


def render_package_services(rows: List[PackageServiceStatus], checked: bool) -> None:
    """
    The package services as a table, and under each one what its list holds after a check.

    Args:
        rows: One status row per package service
        checked: Whether the lists were read
    """
    header = (f'  {"PACKAGE SERVICE":<17} {"DELIVERS":<9} {"SWITCH":<13} {"ENDPOINT":<12} '
              f'{"ADDRESS":<8} CREDENTIAL')
    print('\n' + '=' * 80)
    print(f'📦 Package services — {len(rows)} registered')
    print('=' * 80 + '\n')
    print(header)
    print('  ' + '-' * 78)
    for row in rows:
        print(f'  {row.package_service:<17} {row.delivers.value:<9} '
              f'{_STATE_WORDS[row.state]:<13} {row.endpoint_name:<12} '
              f'{"set" if row.address_set else "—":<8} {row.credential}')
        if checked:
            print(f'      {_list_line(row)}')
    print('\n  An import fetches from the switched-on services with --fetch; each service is '
          'switched in its own peer file.')
    print()


def _list_line(row: PackageServiceStatus) -> str:
    """
    What a checked service's list holds, as one line.

    Args:
        row: The service's status row

    Returns:
        The line, or why its list was not read
    """
    if row.check_note:
        return row.check_note
    return (f'waiting {row.waiting} · acknowledged {row.acknowledged} · last listed '
            f'{row.last_listed_package or "—"}')
