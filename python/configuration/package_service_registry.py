"""
FiniexTestingIDE - Package Service Registry
The package services an import can fetch from (#120): one per peer that serves packages — the data
collector's tick packages, the RAG engine's signal packages. Each peer declares its service in its
own file (its switch, its ladder, its address beside its credential); this is the one list of them,
read by the import and by the status command.
"""

from typing import List

from python.configuration.data_collector_config_manager import DataCollectorConfigManager
from python.configuration.sentiment_config_manager import SentimentConfigManager
from python.framework.types.import_types.package_types import PackageServiceResolution

# The configuration managers whose peer serves packages, in the order they are fetched and listed.
# Each resolves its own service; a peer added here declares a package block in its own file.
PACKAGE_SERVICE_MANAGERS = (DataCollectorConfigManager, SentimentConfigManager)


def resolve_package_services() -> List[PackageServiceResolution]:
    """
    Every package service, as its configuration resolves it.

    Returns:
        One resolution per service — on, switched off, or without an address — in fetch order
    """
    return [manager().resolve_package_source() for manager in PACKAGE_SERVICE_MANAGERS]
