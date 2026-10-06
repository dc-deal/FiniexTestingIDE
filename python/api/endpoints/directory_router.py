"""
Directory API router — every configuration file that can start a run (#554).

The list the run index cannot give: what CAN run, next to what did. One row per scenario set or
AutoTrader profile, including files that never ran — `run_count: 0` is an answer, and a file being
edited that does not parse is a row with its reason rather than an error. Served from the
directory's cache (`ConfigDirectory`), refreshed at most every `FRESHNESS_S` unless `refresh` asks.

A read writes nothing but that cache. The operator's private paths never leave the server: a row
names its file and its origin, not where on disk it lies.
"""

from fastapi import APIRouter, Query

from python.api.api_error_catalog import CONFIG_FILE_NOT_FOUND, api_error
from python.api.api_route_documents import describes
from python.framework.config_directory.config_directory import ConfigDirectory
from python.framework.types.api.directory_types import (
    DirectoryDetailResponse,
    DirectoryListResponse,
)

router = APIRouter()


def _directory() -> ConfigDirectory:
    """The config directory at its configured location."""
    return ConfigDirectory()


@router.get('/directory', response_model=DirectoryListResponse,
            openapi_extra=describes('directory'))
def list_directory(
    refresh: bool = Query(False, description='Walk the roots now instead of serving a directory '
                                             'refreshed within the last few seconds'),
) -> DirectoryListResponse:
    """
    Every configuration file that can start a run, newest-changed first.

    Args:
        refresh: Walk the configuration roots now

    Returns:
        One row per file, with its run figures
    """
    return _directory().list_configs(refresh=refresh)


@router.get('/directory/{file}', response_model=DirectoryDetailResponse,
            openapi_extra=describes('directory'))
def get_directory_entry(file: str) -> DirectoryDetailResponse:
    """
    One configuration file: its row, its scenarios and the runs started from it.

    Args:
        file: The file name, as the list names it

    Returns:
        The detail
    """
    detail = _directory().detail(file)
    if detail is None:
        raise api_error(CONFIG_FILE_NOT_FOUND, file=file)
    return detail
