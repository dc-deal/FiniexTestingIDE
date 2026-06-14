"""
FiniexTestingIDE Data Quality Exceptions
Custom exceptions for data validation and quality issues
"""

from typing import List

from python.framework.exceptions.finiex_error import FiniexError
from python.framework.reporting.duplicate_report import DuplicateReport


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
