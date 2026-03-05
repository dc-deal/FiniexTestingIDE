"""
Test Runner CLI
Unified test runner for all core test suites.

Runs each suite sequentially via subprocess and presents a compact summary.
Configuration loaded from configs/test_config.json.
"""

import argparse
import os
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional

from python.configuration.test_config_loader import TestConfigLoader


@dataclass
class SuiteResult:
    """Result of a single test suite execution."""
    name: str
    passed: int
    failed: int
    errors: int
    skipped: int
    exit_code: int


class TestRunnerCLI:
    """
    CLI handler for unified test execution.
    """

    _SUMMARY_PATTERN = re.compile(
        r'(\d+) passed'
        r'(?:, (\d+) failed)?'
        r'(?:, (\d+) error)?'
        r'(?:, (\d+) skipped)?'
    )

    _SEPARATOR = '\u2500' * 50

    def __init__(self):
        """Initialize CLI handler."""
        pass

    def cmd_run(self) -> None:
        """
        Run all core test suites and print compact summary.
        """
        config = TestConfigLoader()
        excluded = config.get_excluded()
        ignored = config.get_ignored()
        fail_fast = config.is_fail_fast()

        # Discover test suite directories
        tests_dir = Path('tests')
        if not tests_dir.exists():
            print('tests/ directory not found.')
            sys.exit(1)

        skip_dirs = set(excluded + ignored)
        suites = sorted([
            d.name for d in tests_dir.iterdir()
            if d.is_dir()
            and not d.name.startswith('__')
            and d.name not in skip_dirs
        ])

        if not suites:
            print('No test suites found.')
            sys.exit(1)

        print(f"Running {len(suites)} test suites...")
        print(self._SEPARATOR)

        results: List[SuiteResult] = []
        aborted = False

        for suite in suites:
            result = self._run_suite(suite)
            results.append(result)
            self._print_suite_result(result, suites)

            if fail_fast and result.exit_code != 0:
                aborted = True
                break

        # Summary
        print(self._SEPARATOR)
        self._print_summary(results, aborted)

        # Exit with failure if any suite failed
        has_failures = any(r.exit_code != 0 for r in results)
        sys.exit(1 if has_failures else 0)

    def _run_suite(self, suite_name: str) -> SuiteResult:
        """
        Run a single test suite via subprocess.

        Args:
            suite_name: Name of the test suite directory

        Returns:
            SuiteResult with parsed test counts and exit code
        """
        suite_path = f"tests/{suite_name}/"
        proc = subprocess.run(
            [sys.executable, '-m', 'pytest', suite_path, '-v', '--tb=short'],
            capture_output=True,
            text=True,
            cwd=os.getcwd()
        )

        passed, failed, errors, skipped = self._parse_pytest_output(proc.stdout)

        return SuiteResult(
            name=suite_name,
            passed=passed,
            failed=failed,
            errors=errors,
            skipped=skipped,
            exit_code=proc.returncode
        )

    def _parse_pytest_output(self, output: str) -> tuple:
        """
        Parse pytest summary line for test counts.

        Args:
            output: Full pytest stdout

        Returns:
            Tuple of (passed, failed, errors, skipped)
        """
        passed = 0
        failed = 0
        errors = 0
        skipped = 0

        for line in output.splitlines():
            # Match lines like "44 passed", "3 failed, 41 passed", etc.
            match_passed = re.search(r'(\d+) passed', line)
            match_failed = re.search(r'(\d+) failed', line)
            match_errors = re.search(r'(\d+) error', line)
            match_skipped = re.search(r'(\d+) skipped', line)

            if match_passed or match_failed:
                passed = int(match_passed.group(1)) if match_passed else 0
                failed = int(match_failed.group(1)) if match_failed else 0
                errors = int(match_errors.group(1)) if match_errors else 0
                skipped = int(match_skipped.group(1)) if match_skipped else 0

        return passed, failed, errors, skipped

    def _print_suite_result(self, result: SuiteResult, all_suites: List[str]) -> None:
        """
        Print a single suite result line.

        Args:
            result: Suite execution result
            all_suites: All suite names (for column alignment)
        """
        max_name_len = max(len(s) for s in all_suites)
        name_col = result.name.ljust(max_name_len)

        if result.exit_code == 0:
            status = f"{result.passed} passed"
            if result.skipped > 0:
                status += f", {result.skipped} skipped"
            print(f"  {name_col}   {status}")
        else:
            parts = []
            if result.failed > 0:
                parts.append(f"{result.failed} failed")
            if result.errors > 0:
                parts.append(f"{result.errors} errors")
            if result.passed > 0:
                parts.append(f"{result.passed} passed")
            status = ', '.join(parts) if parts else f"exit code {result.exit_code}"
            print(f"  {name_col}   \u274c {status}")

    def _print_summary(self, results: List[SuiteResult], aborted: bool) -> None:
        """
        Print final summary.

        Args:
            results: All collected suite results
            aborted: Whether execution was aborted due to fail_fast
        """
        total_passed = sum(r.passed for r in results)
        total_failed = sum(r.failed for r in results)
        total_errors = sum(r.errors for r in results)
        total_skipped = sum(r.skipped for r in results)
        suites_run = len(results)

        if aborted:
            failed_suite = results[-1].name
            print(f"ABORTED (fail_fast) after {failed_suite}")
            print(f"Suites run: {suites_run}")

        parts = [f"{total_passed} passed"]
        if total_failed > 0:
            parts.append(f"{total_failed} failed")
        if total_errors > 0:
            parts.append(f"{total_errors} errors")
        if total_skipped > 0:
            parts.append(f"{total_skipped} skipped")

        print(f"TOTAL: {', '.join(parts)}")


def main() -> None:
    """CLI entry point for unified test runner."""
    parser = argparse.ArgumentParser(
        description='Unified test runner for all core test suites'
    )
    parser.parse_args()

    cli = TestRunnerCLI()
    cli.cmd_run()


if __name__ == '__main__':
    main()
