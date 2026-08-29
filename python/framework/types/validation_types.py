"""
FiniexTestingIDE - Validation Types
Type definitions for scenario data validation
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Dict, List


class Severity(Enum):
    """Severity of ONE finding — the finding's own, never its container's."""
    ERROR = 'error'         # rejects the unit (§33: the scenario is excluded from execution)
    WARNING = 'warning'     # advisory; the unit still runs


class ValidationDomain(Enum):
    """
    The area a finding belongs to — a closed set, so a consumer can group and filter by it.

    Deliberately an Enum and not a free string: 'profiling' / 'Profiling' / 'perf' would make
    filtering worthless. The identity of the individual check (`ValidationFinding.check`) is an
    open set and stays a string.
    """
    CONFIG = 'config'               # scenario config shape, balances, currencies
    DATA = 'data'                   # availability, coverage, date logic, staleness
    BROKER = 'broker'               # broker configuration and its capabilities
    ALGO = 'algo'                   # worker compatibility, state snapshot, algo clock
    EXECUTION = 'execution'         # the run itself
    SETUP = 'setup'                 # how the run was configured (debug mode, stress test)
    PROFILING = 'profiling'         # tick budget and clipping
    PERFORMANCE = 'performance'     # worker / decision timing, coordination overhead
    PORTFOLIO = 'portfolio'         # accounting, currencies
    ROBUSTNESS = 'robustness'       # robustness / overfit assessment


@dataclass
class ValidationFinding:
    """
    One atomic finding — produced, carried, rendered and filtered as a single unit.

    Args:
        severity: Whether this finding rejects the unit or only advises about it
        check: Stable identifier of the assertion that produced it, e.g. 'budget_too_high'
        domain: The area it belongs to
        message: Operator-readable text
        scope: Which unit it concerns; '' means run-wide
    """
    severity: Severity
    check: str
    domain: ValidationDomain
    message: str
    scope: str = ''


@dataclass
class ValidationResult:
    """
    A subject's validation findings.

    `is_valid`, `errors` and `warnings` are VIEWS over `findings`, not stored state — a stored
    flag can disagree with the list it summarizes, a derived one cannot.
    """
    scenario_name: str
    findings: List[ValidationFinding] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        """True while no finding rejects the unit (the §33 execution gate reads this)."""
        return not any(f.severity is Severity.ERROR for f in self.findings)

    @property
    def errors(self) -> List[str]:
        """The rejecting findings' messages."""
        return [f.message for f in self.findings if f.severity is Severity.ERROR]

    @property
    def warnings(self) -> List[str]:
        """The advisory findings' messages."""
        return [f.message for f in self.findings if f.severity is Severity.WARNING]

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
            return 'No validation errors'

        report_lines = [
            f"Scenario '{self.scenario_name}' failed validation:",
            ''
        ]

        for idx, error in enumerate(self.errors, 1):
            report_lines.append(f'{idx}. {error}')

        if self.warnings:
            report_lines.append('')
            report_lines.append('Warnings:')
            for idx, warning in enumerate(self.warnings, 1):
                report_lines.append(f'  • {warning}')

        return '\n'.join(report_lines)


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
        report_lines = [f"Tick file '{self.file_name}' failed import validation:", '']

        for idx, error in enumerate(self.errors, 1):
            report_lines.append(f'{idx}. {error}')

        if self.warnings:
            report_lines.append('')
            report_lines.append('Warnings:')
            for idx, warning in enumerate(self.warnings, 1):
                report_lines.append(f'  • {warning}')

        return '\n'.join(report_lines)


def get_validation_list_report(validation_list:  List[ValidationResult]) -> str:
    if not validation_list:
        return 'No validation results available'

    reports = []
    for validation in validation_list:
        if not validation.is_valid:
            reports.append(validation.get_full_report())

    if not reports:
        return 'Scenario is valid - Errors may Remain in Scenario Log.'

    return '\n\n'.join(reports)
