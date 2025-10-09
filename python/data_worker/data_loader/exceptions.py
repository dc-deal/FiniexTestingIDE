"""
FiniexTestingIDE Data Quality Exceptions
Custom exceptions for data validation and quality issues

Location: python/data_worker/data_loader/exceptions.py
Version: 1.1 (Enhanced with metadata comparison)
"""

from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple, Dict, Optional
from datetime import datetime


@dataclass
class DuplicateReport:
    """
    Detailed report about detected artificial duplicates

    Contains comparison metrics and recommendations for dealing
    with duplicate Parquet files from the same source.

    Attributes:
        source_file: Name of the original JSON source file
        duplicate_files: List of Parquet files with same source
        tick_counts: Number of ticks in each file
        time_ranges: Time range (start, end) for each file
        file_sizes_mb: File sizes in MB for each file
        metadata: List of dicts with Parquet metadata for each file
    """
    source_file: str
    duplicate_files: List[Path]
    tick_counts: List[int]
    time_ranges: List[Tuple[datetime, datetime]]
    file_sizes_mb: List[float]
    metadata: List[Dict[str, str]]  # NEW: Parquet header metadata

    def get_detailed_report(self) -> str:
        """
        Generate detailed text report with metadata comparison

        Returns:
            Formatted report string with analysis and recommendations
        """
        # Calculate similarity metrics
        tick_counts_identical = len(set(self.tick_counts)) == 1
        time_ranges_identical = len(set(self.time_ranges)) == 1

        # Build report header
        lines = [
            "=" * 80,
            "⚠️  ARTIFICIAL DUPLICATE DETECTED - DATA INTEGRITY VIOLATION",
            "=" * 80,
            "",
            f"📄 Original Source JSON:",
            f"   {self.source_file}",
            "",
            f"📦 Duplicate Parquet Files Found: {len(self.duplicate_files)}",
            ""
        ]

        # File details
        for i, file in enumerate(self.duplicate_files, 1):
            lines.extend([
                f"   [{i}] {file.name}",
                f"       Ticks:     {self.tick_counts[i-1]:>10,}",
                f"       Range:     {self.time_ranges[i-1][0]} → {self.time_ranges[i-1][1]}",
                f"       Size:      {self.file_sizes_mb[i-1]:>10.2f} MB",
                ""
            ])

        # NEW: Metadata Comparison Section
        lines.extend([
            "📋 Parquet Metadata Comparison:",
            ""
        ])

        # Compare each metadata field across all files
        metadata_fields = [
            "source_file", "symbol", "broker", "collector_version",
            "tick_count", "processed_at"
        ]

        for field in metadata_fields:
            values = [meta.get(field, "N/A") for meta in self.metadata]
            is_identical = len(set(values)) == 1

            # Format the comparison
            status = "✅ IDENTICAL" if is_identical else "⚠️  DIFFERENT"
            lines.append(f"   • {field:20s} {status}")

            # Show values if different
            if not is_identical:
                for i, value in enumerate(values, 1):
                    lines.append(f"       [{i}] {value}")

        lines.append("")

        # Data Similarity analysis
        lines.extend([
            "🔬 Data Similarity Analysis:",
            f"   • Tick Counts:  {'✅ IDENTICAL' if tick_counts_identical else '⚠️  DIFFERENT'}",
            f"   • Time Ranges:  {'✅ IDENTICAL' if time_ranges_identical else '⚠️  DIFFERENT'}",
            ""
        ])

        # Severity assessment
        metadata_identical = self._are_metadata_identical()

        if tick_counts_identical and time_ranges_identical and metadata_identical:
            severity = "🔴 CRITICAL - Complete data duplication detected"
            impact = "Impact: Identical files, test results will be severely compromised (2x tick density)"
        elif tick_counts_identical and time_ranges_identical:
            severity = "🟠 HIGH - Data appears identical despite different metadata"
            impact = "Impact: Same tick data, possible re-import with different processing timestamp"
        else:
            severity = "🟡 WARNING - Partial overlap detected"
            impact = "Impact: Test results may be compromised (partial tick duplication)"

        lines.extend([
            f"⚠️  {severity}",
            f"   {impact}",
            ""
        ])

        # Recommendations based on metadata
        lines.extend([
            "💡 Recommended Actions:",
        ])

        if metadata_identical:
            lines.extend([
                "   1. DELETE the duplicate file (both are completely identical)",
                "   2. Keep either file, they contain the exact same data",
                "   3. Re-run the test after cleanup",
            ])
        else:
            lines.extend([
                "   1. CHECK processed_at timestamps to identify the newer file",
                "   2. DELETE the older file (usually the one with earlier processed_at)",
                "   3. If unsure, keep the file with the most recent processed_at timestamp",
                "   4. Re-run the test after cleanup",
            ])

        lines.extend([
            "   5. PREVENT: Never manually copy Parquet files in processed/ directory",
            "",
            "=" * 80
        ])

        return "\n".join(lines)

    def _tick_counts_identical(self) -> bool:
        """Check if all tick counts are identical"""
        return len(set(self.tick_counts)) == 1

    def _time_ranges_identical(self) -> bool:
        """Check if all time ranges are identical"""
        return len(set(self.time_ranges)) == 1

    def _are_metadata_identical(self) -> bool:
        """
        Check if all metadata is identical (except processed_at)

        processed_at is excluded because it changes on re-import
        """
        critical_fields = ["source_file", "symbol",
                           "broker", "collector_version", "tick_count"]

        for field in critical_fields:
            values = [meta.get(field, "N/A") for meta in self.metadata]
            if len(set(values)) != 1:
                return False

        return True


class DataQualityException(Exception):
    """Base exception for all data quality issues"""
    pass


class ArtificialDuplicateException(DataQualityException):
    """
    Raised when artificial duplicates are detected in Parquet files

    Artificial duplicates occur when:
    - Same source JSON is imported multiple times (should overwrite, not duplicate)
    - Parquet files are manually copied in processed/ directory
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

    def __init__(self, invalid_mode: str):
        valid_modes = ["raw", "realistic", "clean"]
        super().__init__(
            f"Invalid data_mode: '{invalid_mode}'. "
            f"Must be one of: {', '.join(valid_modes)}"
        )
