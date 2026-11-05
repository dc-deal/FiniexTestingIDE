"""
Algo Clock Validator (#359) — runtime startup guard.

Verifies the §9 wall-clock scan: the AST core (find_wall_clock_calls), the
class-level collection (collect_algo_clock_violations, dedupe + unresolvable
skip), the raising wrapper (validate_algo_clock), and the centralized batch
pre-flight (RequirementsCollector._algo_clock_preflight) that excludes a
violating scenario while the batch continues. Fixture algos live in
tests/fixtures/algo_clock_validator/ and are loaded the same way the factories
load USER algos (importlib from file path).
"""

import shutil
from pathlib import Path
from types import SimpleNamespace
from typing import Dict

import pytest

from python.framework.batch.requirements_collector import RequirementsCollector
from python.framework.exceptions.algo_clock_errors import AlgoClockViolationError
from python.framework.types.validation_types import Severity
from python.framework.validators.algo_clock_validator import (
    collect_algo_clock_violations,
    find_wall_clock_calls,
    validate_algo_clock,
)
from tests.framework.algo_clock_validator.conftest import FIXTURES_DIR, load_fixture_class


def _scenario(logic_type: str, worker_instances: Dict[str, str] = None) -> SimpleNamespace:
    """Lightweight scenario stand-in — the pre-flight reads only strategy_config."""
    return SimpleNamespace(
        name=f'scenario_{Path(logic_type).stem}',
        strategy_config={
            'decision_logic_type': logic_type,
            'worker_instances': worker_instances or {},
        },
    )


class TestFindWallClockCalls:
    """The AST scanning core — shared with the CI lint."""

    def test_dirty_logic_flagged(self):
        hits = find_wall_clock_calls(FIXTURES_DIR / 'wall_clock_logic.py')
        assert len(hits) == 1
        assert 'datetime.now()' in hits[0]

    def test_dirty_worker_flagged(self):
        hits = find_wall_clock_calls(FIXTURES_DIR / 'wall_clock_worker.py')
        assert len(hits) == 1
        assert 'time.time()' in hits[0]

    def test_clean_logic_passes(self):
        assert find_wall_clock_calls(FIXTURES_DIR / 'clean_logic.py') == []

    def test_no_false_positives_in_strings_and_comments(self, tmp_path):
        # AST-based scan: mentions in comments / strings / docstrings are not calls.
        source = tmp_path / 'mentions_only.py'
        source.write_text(
            '"""Docstring mentioning datetime.now() and time.time()."""\n'
            '# comment: datetime.now()\n'
            "LABEL = 'time.time()'\n"
        )
        assert find_wall_clock_calls(source) == []


class TestCollectAlgoClockViolations:
    """Class-level collection: source resolution, dedupe, unresolvable skip."""

    def test_dirty_class_flagged(self):
        dirty = load_fixture_class('wall_clock_logic.py', 'WallClockLogic')
        violations = collect_algo_clock_violations([dirty])
        assert len(violations) == 1
        assert 'datetime.now()' in violations[0]

    def test_same_source_file_scanned_once(self):
        # The same class twice → one source file → one violation, not two.
        dirty = load_fixture_class('wall_clock_logic.py', 'WallClockLogic')
        assert len(collect_algo_clock_violations([dirty, dirty])) == 1

    def test_builtin_class_skipped(self):
        # No resolvable source file (builtin) → silently skipped, nothing to scan.
        assert collect_algo_clock_violations([dict]) == []


class TestValidateAlgoClock:
    """The raising wrapper used by the AutoTrader startup."""

    def test_clean_classes_pass(self):
        clean = load_fixture_class('clean_logic.py', 'CleanLogic')
        validate_algo_clock([clean])

    def test_dirty_class_raises_with_guidance(self):
        dirty = load_fixture_class('wall_clock_logic.py', 'WallClockLogic')
        with pytest.raises(AlgoClockViolationError) as exc_info:
            validate_algo_clock([dirty])
        message = str(exc_info.value)
        assert 'get_current_time()' in message
        assert 'wall_clock_logic.py' in message


class TestCollectorClockPreflight:
    """RequirementsCollector._algo_clock_preflight — the centralized batch check."""

    def test_clean_logic_passes(self, logger):
        collector = RequirementsCollector(logger=logger)
        scenario = _scenario(str(FIXTURES_DIR / 'clean_logic.py'))
        outcome = collector._algo_clock_preflight(scenario)
        assert outcome.error is None and outcome.skipped is None

    def test_dirty_logic_flagged(self, logger):
        collector = RequirementsCollector(logger=logger)
        scenario = _scenario(str(FIXTURES_DIR / 'wall_clock_logic.py'))
        error = collector._algo_clock_preflight(scenario).error
        assert error is not None
        assert 'get_current_time()' in error

    def test_dirty_worker_flagged(self, logger):
        # Clean logic, but a configured worker reads wall-clock → flagged.
        collector = RequirementsCollector(logger=logger)
        scenario = _scenario(
            str(FIXTURES_DIR / 'clean_logic.py'),
            worker_instances={'w1': str(FIXTURES_DIR / 'wall_clock_worker.py')},
        )
        error = collector._algo_clock_preflight(scenario).error
        assert error is not None
        assert 'time.time()' in error

    def test_no_logic_type_is_noop(self, logger):
        collector = RequirementsCollector(logger=logger)
        outcome = collector._algo_clock_preflight(SimpleNamespace(strategy_config={}))
        assert outcome.error is None and outcome.skipped is None

    def test_unresolvable_logic_skipped(self, logger):
        # A resolution failure excludes nothing — the regular pipeline reports unknown types
        # with full context — but the skip is returned, so it reaches the batch as a warning.
        collector = RequirementsCollector(logger=logger)
        scenario = _scenario('CORE/does_not_exist')
        outcome = collector._algo_clock_preflight(scenario)
        assert outcome.error is None
        assert 'CORE/does_not_exist' in outcome.skipped

    def test_cached_per_distinct_algo_set(self, logger):
        collector = RequirementsCollector(logger=logger)
        scenario = _scenario(str(FIXTURES_DIR / 'wall_clock_logic.py'))
        first = collector._algo_clock_preflight(scenario)
        second = collector._algo_clock_preflight(scenario)
        assert first == second
        assert len(collector._clock_preflight_cache) == 1


def _project_tree(tmp_path: Path, monkeypatch, required_worker: str = '') -> None:
    """
    A bot folder under a stand-in project root, named the way a scenario set names it (#633).

    Args:
        tmp_path: The stand-in project root; the test runs from it
        monkeypatch: To make it the working directory
        required_worker: A worker reference the logic requires, '' for none
    """
    bot = tmp_path / 'my_bot'
    bot.mkdir()
    logic = (FIXTURES_DIR / 'clean_logic.py').read_text()
    if required_worker:
        logic = logic.replace(
            '        return {}\n',
            f"        return {{'w': WorkerRequirement.of('{required_worker}', 'forecast')}}\n")
    (bot / 'my_logic.py').write_text(logic)
    shutil.copy(FIXTURES_DIR / 'wall_clock_worker.py', bot / 'my_worker.py')
    monkeypatch.chdir(tmp_path)


class TestAWorkerNamedFromTheProjectRoot:
    """
    #633: a scenario set names its workers from the project root, as the run creates them.

    The pre-flight used to resolve them against the logic's folder, joined the two paths into one
    that does not exist, and skipped itself at DEBUG — so a user worker was never scanned.
    """

    def test_the_wall_clock_scan_reaches_it(self, logger, tmp_path, monkeypatch):
        _project_tree(tmp_path, monkeypatch)
        outcome = RequirementsCollector(logger=logger)._algo_clock_preflight(
            _scenario('my_bot/my_logic.py', {'w': 'my_bot/my_worker.py'}))
        assert outcome.skipped is None
        assert outcome.error is not None and 'time.time()' in outcome.error

    def test_a_reference_that_resolves_nowhere_is_a_warning(self, logger, tmp_path, monkeypatch):
        _project_tree(tmp_path, monkeypatch)
        scenario = _scenario('my_bot/my_logic.py', {'w': 'my_bot/missing_worker.py'})
        scenario.validation_result = []
        collector = RequirementsCollector(logger=logger)

        outcome = collector._algo_clock_preflight(scenario)
        collector._warn_if_skipped(scenario, outcome)

        assert outcome.error is None
        assert 'my_bot/missing_worker.py' in outcome.skipped
        finding = scenario.validation_result[0].findings[0]
        assert finding.severity is Severity.WARNING
        assert finding.check == 'algo_preflight_skipped'
        # A warning: the scenario still runs, and the run itself reports the missing file
        assert scenario.validation_result[0].is_valid
        assert any('missing_worker.py' in line for line in logger.warnings)

    def test_the_signal_check_resolves_a_required_worker_from_the_logic(
            self, logger, tmp_path, monkeypatch):
        # A logic names the workers it requires relative to its own file; the check reaches the
        # worker and finds the subscribed output missing from its schema
        _project_tree(tmp_path, monkeypatch, required_worker='my_worker.py')
        outcome = RequirementsCollector(logger=logger)._worker_signal_preflight(
            _scenario('my_bot/my_logic.py', {'w': 'my_bot/my_worker.py'}))
        assert outcome.skipped is None
        assert outcome.error is not None and 'forecast' in outcome.error
