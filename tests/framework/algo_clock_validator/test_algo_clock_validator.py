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

from pathlib import Path
from types import SimpleNamespace
from typing import Any, Dict

import pytest

from python.framework.batch.requirements_collector import RequirementsCollector
from python.framework.exceptions.algo_clock_errors import AlgoClockViolationError
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
        assert collector._algo_clock_preflight(scenario) is None

    def test_dirty_logic_flagged(self, logger):
        collector = RequirementsCollector(logger=logger)
        scenario = _scenario(str(FIXTURES_DIR / 'wall_clock_logic.py'))
        error = collector._algo_clock_preflight(scenario)
        assert error is not None
        assert 'get_current_time()' in error

    def test_dirty_worker_flagged(self, logger):
        # Clean logic, but a configured worker reads wall-clock → flagged.
        collector = RequirementsCollector(logger=logger)
        scenario = _scenario(
            str(FIXTURES_DIR / 'clean_logic.py'),
            worker_instances={'w1': str(FIXTURES_DIR / 'wall_clock_worker.py')},
        )
        error = collector._algo_clock_preflight(scenario)
        assert error is not None
        assert 'time.time()' in error

    def test_no_logic_type_is_noop(self, logger):
        collector = RequirementsCollector(logger=logger)
        assert collector._algo_clock_preflight(SimpleNamespace(strategy_config={})) is None

    def test_unresolvable_logic_skipped(self, logger):
        # Resolution failure is a best-effort skip — the regular pipeline
        # validation reports unknown types with full context (§33).
        collector = RequirementsCollector(logger=logger)
        scenario = _scenario('CORE/does_not_exist')
        assert collector._algo_clock_preflight(scenario) is None

    def test_cached_per_distinct_algo_set(self, logger):
        collector = RequirementsCollector(logger=logger)
        scenario = _scenario(str(FIXTURES_DIR / 'wall_clock_logic.py'))
        first = collector._algo_clock_preflight(scenario)
        second = collector._algo_clock_preflight(scenario)
        assert first == second
        assert len(collector._clock_preflight_cache) == 1
