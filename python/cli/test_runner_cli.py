"""
Test Runner CLI
Unified test runner for all core test suites.

Runs the suites through a bounded pool of lanes, each suite in a subprocess of its own, and
presents a compact summary. Configuration loaded from configs/test_config.json.
"""

import argparse
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional, Tuple

from python.configuration.test_config_loader import TestConfigLoader
from python.framework.activities.activity_registry import ActivityRegistry
from python.framework.types.activity_registry_types import ActivityKind
from python.framework.types.run_origin_types import RunChannel
from python.framework.types.test_runner_types import SuiteOutcome, SuiteResult
from python.framework.utils.machine_resources_reader import MachineResourcesReader


class TestRunnerCli:
    """
    CLI handler for unified test execution.
    """

    _SEPARATOR = '\u2500' * 50

    def __init__(self):
        """Initialize CLI handler."""
        # One root per run, filled in before the first suite starts: each suite works in a
        # directory of its own under it.
        self._temp_root: Optional[Path] = None

    def cmd_run(self) -> None:
        """
        Run all core test suites and print compact summary.
        """
        # Listed as running for as long as it runs (#603). Its pytest processes count toward it;
        # what the tests start inside them is listed in an isolated directory, not here.
        with ActivityRegistry().running(ActivityKind.TEST_SUITE, 'full',
                                        channel=RunChannel.CLI) as full_activity_id:
            self._run_suites(full_activity_id)

    def _run_suites(self, full_activity_id: Optional[str]) -> None:
        """
        Discover and run every core suite, while the suite run is listed.

        Args:
            full_activity_id: The activity the whole run is listed under, so each suite can name
                it as its parent instead of reading a stack several lanes share
        """
        config = TestConfigLoader()
        excluded = config.get_excluded()
        ignored = config.get_ignored()
        fail_fast = config.is_fail_fast()

        # Discover test suite directories (2-level: group/suite)
        tests_dir = Path('tests')
        if not tests_dir.exists():
            print('tests/ directory not found.')
            sys.exit(1)

        skip_dirs = set(excluded + ignored)
        # Each entry: (display_name, list of pytest path args)
        suites: List[tuple] = []
        for group_dir in sorted(tests_dir.iterdir()):
            if not group_dir.is_dir() or group_dir.name.startswith('__'):
                continue
            if group_dir.name in skip_dirs:
                continue

            subdirs = [
                d for d in sorted(group_dir.iterdir())
                if d.is_dir() and not d.name.startswith('__')
            ]
            loose_files = [
                f for f in sorted(group_dir.iterdir())
                if f.is_file() and f.name.startswith('test_') and f.suffix == '.py'
            ]

            if not subdirs:
                # Flat suite: only loose test files at group level
                suites.append((group_dir.name, [f'tests/{group_dir.name}/']))
                continue

            # Sub-suites as individual entries
            for suite_dir in subdirs:
                full_name = f'{group_dir.name}/{suite_dir.name}'
                if suite_dir.name in skip_dirs or full_name in skip_dirs:
                    continue
                suites.append((full_name, [f'tests/{full_name}/']))

            # Loose top-level test files become a synthetic <group>/_root suite
            if loose_files:
                root_name = f'{group_dir.name}/_root'
                if '_root' in skip_dirs or root_name in skip_dirs:
                    continue
                suites.append((root_name, [str(f) for f in loose_files]))

        suites.sort(key=lambda s: s[0])

        if not suites:
            print('No test suites found.')
            sys.exit(1)

        start_dt = datetime.now(timezone.utc)
        print(f'Running {len(suites)} test suites...')
        print(f'Started:  {self._format_clock(start_dt)}')
        print(self._SEPARATOR)

        max_name_len = max(len(name) for name, _ in suites)
        lanes = self._effective_lanes(config.get_lanes())
        timeout_s = config.get_suite_timeout_seconds()
        print(f'Lanes:    {lanes}')
        print(self._SEPARATOR)

        # Not `finiex_suite_`: the test conftest already makes `finiex_suite_global_log_`
        # directories, and one name being a prefix of the other makes them indistinguishable
        # to anyone listing the folder.
        self._temp_root = Path(tempfile.mkdtemp(prefix='finiex_lanes_'))
        wall_start = time.monotonic()
        results = self._run_in_lanes(suites, lanes, timeout_s, fail_fast, max_name_len,
                                     full_activity_id)
        wall_duration = time.monotonic() - wall_start

        # Back into the order the suites were discovered in: results arrive as they finish, and a
        # summary a reader scans for a name must not depend on which lane was quickest.
        by_name = {r.name: r for r in results}
        ordered = [by_name[name] for name, _ in suites if name in by_name]

        print(self._SEPARATOR)
        self._print_summary(ordered, wall_duration)
        failed = any(r.outcome.is_failure() for r in ordered)
        self._clear_temp_root(keep=failed)
        print(f'Finished: {self._format_clock(datetime.now(timezone.utc))}')

        sys.exit(1 if failed else 0)

    def _effective_lanes(self, configured: int) -> int:
        """
        Decide how many suites may run side by side right now.

        The configured value is a ceiling, not a count. The machine sets the other one, so a
        second suite running beside this one lowers the number by itself instead of turning a
        shared machine into a queue. Never below one: a run that starts nothing is worse than a
        slow one.

        Args:
            configured: The ceiling from the configuration

        Returns:
            The number of lanes to start
        """
        # The busy share is the difference between two samples, and the reader takes its first
        # when it is built — so a reading taken in the same breath has no window to measure and
        # reports nothing at all. A short pause buys a real figure, once per run.
        reader = MachineResourcesReader()
        time.sleep(0.3)
        reading = reader.read()
        # Still None if the clock did not move; then nothing is known to be busy, which is the
        # reading that starts the most lanes rather than the fewest.
        busy_share = (reading.cpu.used_pct or 0.0) / 100.0
        free_processors = int(reading.cpu.count * (1.0 - busy_share))
        free_bytes = reading.memory.limit_bytes - reading.memory.used_bytes
        # Measured over the four heaviest suites: a lane adds well under a gigabyte of its own,
        # because the python runtime and the imported framework are shared. Two is the headroom
        # that keeps the figure a budget rather than a guess at the margin.
        free_lanes_by_memory = int(free_bytes / (2 * 1024 ** 3))
        return max(1, min(configured, free_processors, free_lanes_by_memory))

    def _run_in_lanes(self,
                      suites: List[tuple],
                      lanes: int,
                      timeout_s: int,
                      fail_fast: bool,
                      max_name_len: int,
                      full_activity_id: Optional[str]) -> List[SuiteResult]:
        """
        Run every suite through a bounded pool, printing each result as it lands.

        Args:
            suites: The discovered suites as (display name, pytest path args)
            lanes: How many may run at once
            timeout_s: How long one suite may run before it is given up on
            fail_fast: Whether a failure stops further suites from starting
            max_name_len: Max suite name length for column alignment
            full_activity_id: The activity each suite is listed under

        Returns:
            One result per suite, in the order they finished
        """
        stop_starting = threading.Event()
        print_lock = threading.Lock()
        total = len(suites)

        def run_one(numbered: Tuple[int, Tuple[str, List[str]]]) -> SuiteResult:
            position, (suite_name, pytest_paths) = numbered
            if stop_starting.is_set():
                return self._not_started(suite_name)
            # Listed under the full run while it runs (#603), with its place in the run. The place
            # is its position among the discovered suites, not the order it happened to start in:
            # with several lanes that order is a race, and a label is read by a person.
            # The parent is named rather than taken from the stack the registry keeps, because
            # that stack is shared and would hand one suite another as its parent.
            with ActivityRegistry().running(ActivityKind.TEST_SUITE,
                                            f'{suite_name} ({position}/{total})',
                                            channel=RunChannel.CLI,
                                            parent_activity_id=full_activity_id):
                result = self._run_suite(suite_name, pytest_paths, timeout_s)
            if fail_fast and result.outcome.is_failure():
                stop_starting.set()
            with print_lock:
                self._print_suite_result(result, max_name_len)
            return result

        with ThreadPoolExecutor(max_workers=lanes) as pool:
            return list(pool.map(run_one, enumerate(suites, start=1)))

    @staticmethod
    def _not_started(suite_name: str) -> SuiteResult:
        """
        The result of a suite the run never reached.

        Its counts are zero because nothing was counted, not because nothing passed — which is
        why the outcome, not the numbers, is what the summary reads.

        Args:
            suite_name: Display name of the suite

        Returns:
            A result marked as never started
        """
        return SuiteResult(name=suite_name, passed=0, failed=0, errors=0, skipped=0,
                           exit_code=0, duration=0.0, failed_tests=[],
                           outcome=SuiteOutcome.NOT_STARTED)

    def _run_suite(self, suite_name: str, pytest_paths: List[str],
                   timeout_s: int) -> SuiteResult:
        """
        Run a single test suite via subprocess.

        Args:
            suite_name: Display name of the suite
            pytest_paths: Explicit pytest path args (directory or file list)
            timeout_s: How long the suite may run before it is given up on

        Returns:
            SuiteResult with parsed test counts and how the run ended
        """
        start = time.monotonic()
        # The cache provider is off, not merely unused: it writes one `.pytest_cache` in the
        # working directory, and several suites running at once would be writing the same file.
        # Nothing here reads it — the counts come from what pytest prints.
        command = [sys.executable, '-m', 'pytest', *pytest_paths, '-v', '--tb=short',
                   '-p', 'no:cacheprovider',
                   '--basetemp', str(self._basetemp_for(suite_name))]
        try:
            proc = subprocess.run(command, capture_output=True, text=True,
                                  cwd=os.getcwd(), timeout=timeout_s)
            stdout, returncode = proc.stdout, proc.returncode
            outcome = SuiteOutcome.from_exit_code(returncode)
        except subprocess.TimeoutExpired as expired:
            # The process is already killed by the time this is raised; what it managed to write
            # is still worth parsing, because it says how far the suite got before it stopped.
            stdout = expired.stdout.decode(errors='replace') if expired.stdout else ''
            returncode = -1
            outcome = SuiteOutcome.TIMED_OUT

        duration = time.monotonic() - start
        passed, failed, errors, skipped = self._parse_pytest_output(stdout)
        failed_tests = self._parse_failed_tests(stdout)

        return SuiteResult(
            name=suite_name,
            passed=passed,
            failed=failed,
            errors=errors,
            skipped=skipped,
            exit_code=returncode,
            duration=duration,
            failed_tests=failed_tests,
            outcome=outcome
        )

    def _clear_temp_root(self, keep: bool) -> None:
        """
        Remove what the suites wrote to their temporary directories, or say where it was kept.

        pytest empties a directory it is handed and never rotates it, so a run that left its own
        root behind would add one per run and clean none. A run that found something keeps it:
        that is where the evidence sits.

        Args:
            keep: Whether to leave the directories in place for someone to look at
        """
        if self._temp_root is None:
            return
        if keep:
            print(f'Temp:     {self._temp_root} (kept \u2014 a suite did not pass)')
            return
        shutil.rmtree(self._temp_root, ignore_errors=True)

    def _basetemp_for(self, suite_name: str) -> Path:
        """
        The temporary directory this suite's pytest process works in.

        Left to itself, pytest numbers its own base directory under a shared root and rotates the
        older ones. Several processes starting in the same second would be competing over that
        counter, so each suite is handed a directory of its own instead — named after the suite,
        under one root per run, which also makes a leftover directory say which suite left it.

        Args:
            suite_name: Display name of the suite

        Returns:
            The directory to hand pytest as its base temp
        """
        return self._temp_root / suite_name.replace('/', '_')

    def _parse_failed_tests(self, output: str) -> List[str]:
        """
        Extract failing test node IDs from the pytest short-summary block.

        Args:
            output: Full pytest stdout

        Returns:
            List of 'FAILED <nodeid> - <reason>' lines (reason truncated)
        """
        failed: List[str] = []
        in_summary = False
        for line in output.splitlines():
            if 'short test summary info' in line:
                in_summary = True
                continue
            if in_summary:
                stripped = line.strip()
                if stripped.startswith('FAILED ') or stripped.startswith('ERROR '):
                    # Truncate overly long reason text for console readability
                    if len(stripped) > 200:
                        stripped = stripped[:197] + '...'
                    failed.append(stripped)
                elif stripped.startswith('=') and stripped.endswith('='):
                    break
        return failed

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

    def _print_suite_result(self, result: SuiteResult, max_name_len: int) -> None:
        """
        Print a single suite result line, overwriting the "running..." indicator.

        Args:
            result: Suite execution result
            max_name_len: Max suite name length for column alignment
        """
        name_col = result.name.ljust(max_name_len)
        duration = self._format_duration(result.duration)

        if result.outcome is SuiteOutcome.PASSED:
            if result.passed == 0 and result.skipped == 0:
                status = 'no tests'
            else:
                status = f'{result.passed} passed'
                if result.skipped > 0:
                    status += f', {result.skipped} skipped'
            print(f'  {name_col}   {status}  ({duration})')
            return

        parts = []
        if result.failed > 0:
            parts.append(f'{result.failed} failed')
        if result.errors > 0:
            parts.append(f'{result.errors} errors')
        if result.passed > 0:
            parts.append(f'{result.passed} passed')
        # Anything but failing tests says WHY no verdict came, because the counts then describe
        # a fragment of the suite and reading them as its result is how a dead lane looks like
        # a defect in the code.
        if result.outcome is not SuiteOutcome.TESTS_FAILED:
            reason = result.outcome.value.replace('_', ' ')
            parts.insert(0, f'{reason} (exit {result.exit_code})')
        status = ', '.join(parts) if parts else f'exit code {result.exit_code}'
        print(f'  {name_col}   \u274c {status}  ({duration})')

    def _print_summary(self, results: List[SuiteResult], wall_duration: float) -> None:
        """
        Print final summary with total duration and per-category breakdown.

        Args:
            results: All collected suite results, in discovery order
            wall_duration: How long the whole run took on the clock
        """
        counted = [r for r in results if r.outcome.reached_a_verdict()]
        no_verdict = [r for r in results
                      if not r.outcome.reached_a_verdict()
                      and r.outcome is not SuiteOutcome.NOT_STARTED]
        never_started = [r for r in results if r.outcome is SuiteOutcome.NOT_STARTED]

        total_passed = sum(r.passed for r in counted)
        total_failed = sum(r.failed for r in counted)
        total_errors = sum(r.errors for r in counted)
        total_skipped = sum(r.skipped for r in counted)
        across_suites = sum(r.duration for r in results)

        # Print failing test details for every failed suite
        for result in results:
            if result.outcome.is_failure() and result.failed_tests:
                print(f'  {result.name}:')
                for entry in result.failed_tests:
                    print(f'    {entry}')

        for result in no_verdict:
            print(f'  {result.name}: {result.outcome.value.replace("_", " ")} '
                  f'(exit {result.exit_code}) \u2014 its counts describe a fragment, not the suite')

        if never_started:
            # No suite is named as the cause: with several lanes in flight the one that failed
            # first is not necessarily the one a reader would guess from the order, and naming
            # the wrong one sends them to the wrong place.
            print('ABORTED (fail_fast) \u2014 no further suites were started')

        # Three states, never two: a suite that reached a verdict, one that ran without reaching
        # one, and one the run never got to. Folding the last two into the first would report
        # work nobody did as work that passed.
        print(f'Suites: {len(counted)} with a verdict'
              + (f', {len(no_verdict)} without' if no_verdict else '')
              + (f', {len(never_started)} never started' if never_started else ''))

        parts = [f'{total_passed} passed']
        if total_failed > 0:
            parts.append(f'{total_failed} failed')
        if total_errors > 0:
            parts.append(f'{total_errors} errors')
        if total_skipped > 0:
            parts.append(f'{total_skipped} skipped')

        # The clock, not the sum: with several lanes the two diverge, and the sum of suite
        # durations after a six-minute run would read as the run having taken twenty-four.
        print(f"TOTAL: {', '.join(parts)}  ({self._format_duration(wall_duration)} on the clock, "
              f'{self._format_duration(across_suites)} across suites)')

        # Per-category breakdown (grouped by first path component)
        categories: dict = {}
        for r in counted:
            cat = r.name.split('/')[0]
            if cat not in categories:
                categories[cat] = {'passed': 0, 'failed': 0, 'errors': 0}
            categories[cat]['passed'] += r.passed
            categories[cat]['failed'] += r.failed
            categories[cat]['errors'] += r.errors

        if len(categories) > 1:
            print()
            for cat in sorted(categories):
                counts = categories[cat]
                cat_parts = [f"{counts['passed']} passed"]
                if counts['failed'] > 0:
                    cat_parts.append(f"{counts['failed']} failed")
                if counts['errors'] > 0:
                    cat_parts.append(f"{counts['errors']} errors")
                print(f"  {cat:<16} {', '.join(cat_parts)}")

    @staticmethod
    def _format_clock(dt: datetime) -> str:
        """
        Format a wall-clock timestamp for console output.

        Args:
            dt: Timezone-aware datetime (UTC)

        Returns:
            Formatted string (e.g. "2026-06-17 14:30:05 UTC")
        """
        return dt.strftime('%Y-%m-%d %H:%M:%S UTC')

    @staticmethod
    def _format_duration(seconds: float) -> str:
        """
        Format duration in human-readable form.

        Args:
            seconds: Duration in seconds

        Returns:
            Formatted string (e.g. "4s", "1m 23s")
        """
        if seconds < 60:
            return f'{seconds:.0f}s'
        minutes = int(seconds // 60)
        secs = int(seconds % 60)
        return f'{minutes}m {secs:02d}s'


def main() -> None:
    """CLI entry point for unified test runner."""
    parser = argparse.ArgumentParser(
        description='Unified test runner for all core test suites'
    )
    parser.parse_args()

    cli = TestRunnerCli()
    cli.cmd_run()


if __name__ == '__main__':
    main()
