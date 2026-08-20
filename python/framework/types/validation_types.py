"""
FiniexTestingIDE - Validation Types
Type definitions for scenario data validation
"""

from dataclasses import dataclass, field
from typing import Dict, List


@dataclass
class ValidationResult:
    """
    Result of scenario validation.

    Contains validation status, scenario name, and any errors/warnings.
    """
    is_valid: bool
    scenario_name: str
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)

    def has_errors(self) -> bool:
        """Check if validation has errors."""
        return len(self.errors) > 0

    def has_warnings(self) -> bool:
        """Check if validation has warnings."""
        return len(self.warnings) > 0

    def get_full_report(self) -> str:
        """
        Generate detailed error report for invalid scenario.

        Returns comprehensive summary of all validation errors
        with actionable information for user.

        Returns:
            Multi-line formatted error report
        """
        if not self.errors:
            return "No validation errors"

        report_lines = [
            f"Scenario '{self.scenario_name}' failed validation:",
            ""
        ]

        for idx, error in enumerate(self.errors, 1):
            report_lines.append(f"{idx}. {error}")

        if self.warnings:
            report_lines.append("")
            report_lines.append("Warnings:")
            for idx, warning in enumerate(self.warnings, 1):
                report_lines.append(f"  • {warning}")

        return "\n".join(report_lines)


@dataclass
class TickFileValidationResult:
    """
    Result of validating one tick file at import time.

    Errors reject the file, warnings let it pass. Metrics carry values the
    validator measured but does not judge (burst structure, lag position).
    """
    is_valid: bool
    file_name: str
    errors: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    metrics: Dict[str, float] = field(default_factory=dict)

    def add_error(self, message: str) -> None:
        """
        Record a rejection reason and mark the file invalid.

        Args:
            message: What the file violated, in operator-readable form
        """
        self.errors.append(message)
        self.is_valid = False

    def add_warning(self, message: str) -> None:
        """
        Record a finding that does not reject the file.

        Args:
            message: What was noticed
        """
        self.warnings.append(message)

    def get_full_report(self) -> str:
        """
        Generate the rejection report for an invalid tick file.

        Returns:
            Multi-line formatted report
        """
        report_lines = [f"Tick file '{self.file_name}' failed import validation:", ""]

        for idx, error in enumerate(self.errors, 1):
            report_lines.append(f"{idx}. {error}")

        if self.warnings:
            report_lines.append("")
            report_lines.append("Warnings:")
            for idx, warning in enumerate(self.warnings, 1):
                report_lines.append(f"  • {warning}")

        return "\n".join(report_lines)


def get_validation_list_report(validation_list:  List[ValidationResult]) -> str:
    if not validation_list:
        return "No validation results available"

    reports = []
    for validation in validation_list:
        if not validation.is_valid:
            reports.append(validation.get_full_report())

    if not reports:
        return "Scenario is valid - Errors may Remain in Scenario Log."

    return "\n\n".join(reports)
