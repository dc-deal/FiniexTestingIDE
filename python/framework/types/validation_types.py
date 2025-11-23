"""
FiniexTestingIDE - Validation Types
Type definitions for scenario data validation
"""

from dataclasses import dataclass, field
from typing import List


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
