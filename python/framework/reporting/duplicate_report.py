"""
FiniexTestingIDE Duplicate Report
Detailed report payload for detected artificial duplicate Parquet files.
"""

from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple, Dict
from datetime import datetime

from python.configuration.app_config_manager import AppConfigManager


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
    metadata: List[Dict[str, str]]  # Parquet header metadata

    def get_detailed_report(self) -> str:
        """
        Generate detailed text report with metadata comparison

        Returns:
            Formatted report string with analysis and recommendations
        """
        # Calculate similarity metrics
        tick_counts_identical = self._tick_counts_identical()
        time_ranges_identical = self._time_ranges_identical()

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

        # File details with data_collector paths
        for i, file in enumerate(self.duplicate_files, 1):
            app_config_manager = AppConfigManager()
            data_dir = app_config_manager.get_data_processed_path()
            # Extract and display data_collector path
            relative_path = str(file).split(
                data_dir)[-1] if data_dir in str(file) else file.name

            lines.extend([
                # Show full path including data_collector
                f"   [{i}] {relative_path}",
                f"       Ticks:     {self.tick_counts[i-1]:>10,}",
                f"       Range:     {self.time_ranges[i-1][0]} → {self.time_ranges[i-1][1]}",
                f"       Size:      {self.file_sizes_mb[i-1]:>10.2f} MB",
                ""
            ])

        # Metadata Comparison Section with data_collector
        lines.extend([
            "📋 Parquet Metadata Comparison:",
            ""
        ])

        # Compare each metadata field across all files
        metadata_fields = [
            "source_file", "symbol", "broker_type", "broker", "data_format_version",
            "tick_count", "processed_at"
        ]

        for field in metadata_fields:
            values = [meta.get(field, "N/A") for meta in self.metadata]
            is_identical = len(set(values)) == 1

            # Format the comparison
            status = "✅ IDENTICAL" if is_identical else "⚠️  DIFFERENT"

            # Special highlighting for data_collector field
            if field == "broker_type":
                if not is_identical:
                    status = "⚠️  CROSS-BROKER DUPLICATE!"
                lines.append(f"   • {field:20s} {status}")
            else:
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

        # Enhanced severity assessment considering data_collector
        metadata_identical = self._are_metadata_identical()
        broker_types = [meta.get('broker_type') or meta.get(
            'data_collector', 'unknown') for meta in self.metadata]

        cross_broker = len(set(broker_types)) > 1

        if cross_broker:
            severity = "🔴 CRITICAL - Cross-Broker Duplication"
            impact = f"Impact: Same data imported under different broker_types: {', '.join(set(broker_types))}"

        elif tick_counts_identical and time_ranges_identical and metadata_identical:
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

        # Enhanced recommendations considering data_collector
        lines.extend([
            "💡 Recommended Actions:",
        ])

        if cross_broker:
            lines.extend([
                "   1. INVESTIGATE why the same source was imported under different broker_types",
                "   2. DELETE one of the duplicate files (choose the wrong collector)",
                "   3. Check your import workflow to prevent cross-broker duplicates",
                "   4. Rebuild index after cleanup",
            ])
        elif metadata_identical:
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
            "   6. Rebuild index after cleanup: python python/cli/data_index_cli.py rebuild",
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
        Check if all metadata is identical (except processed_at and broker_type)

        processed_at is excluded because it changes on re-import
        broker_type is excluded because cross-broker duplicates are still duplicates
        """
        # Exclude data_collector from comparison
        critical_fields = ["source_file", "symbol",
                           "broker", "data_format_version", "tick_count"]

        for field in critical_fields:
            values = [meta.get(field, "N/A") for meta in self.metadata]
            if len(set(values)) != 1:
                return False

        return True
