"""
FiniexTestingIDE - Indicator Series Errors (#379)

IndicatorSeriesDeclarationError: a worker declares an indicator series the chart cannot draw — a plot
of an output the worker does not have, an output that is not a number, a further series without a
layer name. Raised when the worker is created, before a run starts, so a mistake in a user's worker
file shows at once rather than as a missing pane after the run.
"""

from python.framework.exceptions.finiex_error import FiniexError


class IndicatorSeriesDeclarationError(FiniexError, ValueError):
    """A worker's declared indicator series cannot be drawn as it stands."""

    def __init__(self, worker_type: str, detail: str):
        self.worker_type = worker_type
        super().__init__(
            f"Worker '{worker_type}' declares an indicator series that cannot be drawn: {detail}. "
            "Fix get_indicator_series() — see the section on drawing a worker's outputs in "
            'docs/user_guides/worker_naming_doc.md.'
        )
