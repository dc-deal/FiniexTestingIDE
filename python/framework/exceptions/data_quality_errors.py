"""
FiniexTestingIDE Data Quality Exceptions
Custom exceptions for data validation and quality issues
"""

from typing import List

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
        super().__init__(f"\n\n{report.get_detailed_report()}")


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
        super().__init__(f"\n\n{result.get_full_report()}")


class InvalidDataModeException(DataQualityException):
    """
    Raised when an invalid data_mode is specified

    Valid data_modes are:
    - "raw": Keep all duplicates (maximum realism)
    - "realistic": Remove duplicates (normal testing)
    - "clean": Remove duplicates (clean testing)
    """

    def __init__(self, invalid_mode: str, valid_modes: List[str]):
        super().__init__(
            f"Invalid data_mode: '{invalid_mode}'. "
            f"Must be one of: {', '.join(valid_modes)}"
        )
