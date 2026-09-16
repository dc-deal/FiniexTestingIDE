"""
FiniexTestingIDE Data Quality Exceptions
Custom exceptions for data validation and quality issues
"""


from python.framework.exceptions.finiex_error import FiniexError
from python.framework.reporting.duplicate_report import DuplicateReport
from python.framework.types.validation_types import TickFileValidationResult


class DataQualityException(FiniexError):
    """Base exception for all data quality issues"""
    pass


class ArtificialDuplicateException(DataQualityException):
    """
    Raised when artificial duplicates are detected in Parquet files

    Artificial duplicates occur when:
    - Same source JSON is imported multiple times (should overwrite, not duplicate)
    - Parquet files are manually copied in processed/ directory
    - Same data imported under different data_collectors
    - File system issues cause duplication

    This exception includes a detailed DuplicateReport for analysis.

    Attributes:
        report: DuplicateReport instance with detailed information
    """

    def __init__(self, report: DuplicateReport):
        self.report = report
        super().__init__(f'\n\n{report.get_detailed_report()}')


class TickFileValidationException(DataQualityException):
    """
    Raised when a tick JSON file violates a structural import invariant

    The importer validates and refuses — it never repairs. A file that fails
    here is rejected as a single-file failure; the batch continues.

    Attributes:
        result: TickFileValidationResult with all findings for this file
    """

    def __init__(self, result: TickFileValidationResult):
        self.result = result
        super().__init__(f'\n\n{result.get_full_report()}')


class BarFileVerificationException(DataQualityException):
    """
    Raised when a freshly written bar file cannot be read back in full

    A truncated or partially written column is invisible to every check that
    opens the file without decoding it: the footer still reports the correct
    row count, and a projection of a single column reads cleanly. The file is
    therefore read back completely right after writing, while the cause is
    still known — a day later it surfaces as a missing index row.
    """
    pass


class TradedPriceMissingException(DataQualityException):
    """
    Raised when a venue that prints trades delivers ticks without a traded price

    An order-driven venue has a central order book, so `last` is a real event on every
    tick — and the import refuses a file where it is absent. Reaching the tick loop
    without one therefore means this pipeline dropped it somewhere between the archive
    and the mount, and the consequence is silent: `TickData.price` falls back to the
    book midpoint, so every bar of the run is built on a different basis than the
    archive it was rendered from.

    Invisible on data below collector format 1.6.0, where the midpoint and the traded
    price are the same number, and total from the first file that carries a real spread.
    """
    pass
