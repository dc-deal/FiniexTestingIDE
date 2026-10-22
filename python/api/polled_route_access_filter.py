"""
FiniexTestingIDE - Polled Route Access Filter (#595)

Keeps the successful answers of a route made to be polled out of the server's access log. A client
asking every few seconds writes a line per reading otherwise, and the requests that carry meaning
disappear between them. A FAILED answer on the same route stays in the log: the log is read when
something went wrong, and that is the one line worth keeping.
"""

import logging
from typing import FrozenSet, List, Tuple

# The logger uvicorn writes one line per answered request to
ACCESS_LOGGER_NAME = 'uvicorn.access'

# An access record carries five arguments: client, method, path with its query, HTTP version and
# status — the same in both of uvicorn's HTTP implementations
_ACCESS_RECORD_ARGS = 5


class PolledRouteAccessFilter(logging.Filter):
    """
    Drops an access-log line when its path is a polled route and its answer is not an error.

    Args:
        polled_paths: The full paths of the routes made to be polled, without a query, as the
            application mounts them — a segment in braces, such as `{run_id}`, stands for any one
            segment of a requested path
    """

    def __init__(self, polled_paths: FrozenSet[str]):
        super().__init__()
        self._exact_paths = frozenset(path for path in polled_paths if '{' not in path)
        self._templates: Tuple[List[str], ...] = tuple(
            path.split('/') for path in polled_paths if '{' in path)

    def filter(self, record: logging.LogRecord) -> bool:
        """
        Decide whether one access-log line is written.

        Args:
            record: The log record uvicorn emits for an answered request

        Returns:
            False for a successful answer on a polled route, True for everything else
        """
        args = record.args
        # A record not shaped like an access line is not this filter's to judge
        if not isinstance(args, tuple) or len(args) != _ACCESS_RECORD_ARGS:
            return True
        path = str(args[2]).partition('?')[0]
        status = args[4]
        return not (self._is_polled(path) and isinstance(status, int) and status < 400)

    def _is_polled(self, path: str) -> bool:
        """
        Whether a requested path is one of the polled routes.

        Args:
            path: The requested path, without its query

        Returns:
            True when it equals a polled path, or fills a template's braced segments
        """
        if path in self._exact_paths:
            return True
        segments = path.split('/')
        return any(
            len(template) == len(segments)
            and all(part.startswith('{') or part == segment
                    for part, segment in zip(template, segments))
            for template in self._templates)

    @staticmethod
    def install(polled_paths: FrozenSet[str]) -> None:
        """
        Put the filter on uvicorn's access logger, replacing one installed before.

        The app factory runs more than once in one process — under `--reload`, and once per test —
        so an earlier filter is replaced rather than stacked beside the new one.

        Args:
            polled_paths: The full paths of the routes made to be polled
        """
        access_logger = logging.getLogger(ACCESS_LOGGER_NAME)
        for installed in [f for f in access_logger.filters
                          if isinstance(f, PolledRouteAccessFilter)]:
            access_logger.removeFilter(installed)
        access_logger.addFilter(PolledRouteAccessFilter(polled_paths))
