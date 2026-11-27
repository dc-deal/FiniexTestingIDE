"""
Which changed paths a backtest may execute (#641) — the declared rule, and the rule held to what
the shipped scenario sets and profiles actually load.
"""

import json
from pathlib import Path
from typing import Iterator, List

import pytest

from python.framework.behaviour_comparison.changed_path_classifier import (
    classify_changed_paths,
    is_executable_path,
    same_python_code,
)
from python.framework.types.behaviour_comparison_types import ComparedRepository, ComparisonEnding

_ROOT = Path(__file__).resolve().parents[3]


class TestTheDeclaredRule:

    @pytest.mark.parametrize('path', ['docs/guide.md', 'tests/framework/test_x.py', 'README.md',
                                      'user_algos_notes.md', '.vscode/launch.json'])
    def test_documents_and_tests_are_not_executed(self, path):
        assert not is_executable_path(path)

    @pytest.mark.parametrize('path', ['python/framework/x.py', 'configs/app_config.json',
                                      'requirements.txt', '.gitignore', 'my_algo/strategy.py',
                                      'tests/fixtures/run_entry_census/census_probe_decision.py'])
    def test_code_configuration_and_fixtures_are_executable(self, path):
        assert is_executable_path(path)

    def test_a_path_the_rule_does_not_name_counts_as_executable(self):
        assert is_executable_path('something_new/added.bin')

    def test_the_paths_are_sorted_and_the_rest_named_by_kind(self):
        paths = classify_changed_paths(ComparedRepository.FRAMEWORK,
                                       ['docs/a.md', 'docs/b.md', 'tests/t.py', 'python/x.py'])
        assert paths.executable == ['python/x.py']
        assert paths.not_executed == ['docs/a.md', 'docs/b.md', 'tests/t.py']
        assert paths.not_executed_kinds == ['docs 2', 'tests 1']

    def test_a_strategy_repository_skips_nothing(self):
        paths = classify_changed_paths(ComparedRepository.STRATEGY,
                                       ['docs/notes.md', 'tests/test_x.py', 'my_algo/x.py'])
        assert paths.executable == ['docs/notes.md', 'tests/test_x.py', 'my_algo/x.py']
        assert paths.not_executed == [] and paths.changed_path_count == 3

    def test_a_comment_only_file_leaves_the_executable_paths_in_every_repository(self):
        for repository in ComparedRepository:
            paths = classify_changed_paths(repository, ['my_algo/x.py', 'my_algo/y.py'],
                                           comment_only=['my_algo/x.py'])
            assert paths.executable == ['my_algo/y.py']
            assert paths.comment_only == ['my_algo/x.py']
            assert paths.not_executed_kinds == ['comment only 1']

    def test_only_the_framework_has_a_declared_layout(self):
        declared = {repository for repository in ComparedRepository
                    if repository.has_declared_layout()}
        assert declared == {ComparedRepository.FRAMEWORK}


_CODE = 'def size(lots, scale):\n    return lots * scale\n'


class TestTheSyntaxTree:

    @pytest.mark.parametrize('new', [
        'def size(lots, scale):\n    # the account\'s scale\n    return lots * scale\n',
        'def size(lots, scale):\n\n    return lots * scale  \n',
        'def size(lots,\n         scale):\n    return lots * scale\n',
    ])
    def test_comments_blank_lines_and_wrapping_are_the_same_code(self, new):
        assert same_python_code(_CODE, new)

    @pytest.mark.parametrize('new', [
        'def size(lots, scale):\n    \"\"\"Sized.\"\"\"\n    return lots * scale\n',
        'def size(lots, scale):\n    return scale * lots\n',
        'def size(lots, scale):\n    return lots * scale * 0.99\n',
    ])
    def test_a_docstring_or_an_expression_is_another_code(self, new):
        assert not same_python_code(_CODE, new)

    def test_a_file_that_does_not_parse_or_does_not_exist_is_another_code(self):
        assert not same_python_code(_CODE, 'def size(:\n')
        assert not same_python_code(None, _CODE) and not same_python_code(_CODE, None)


def _strategy_paths(document: object) -> Iterator[str]:
    """
    Every decision logic and worker a configuration document names by file path.

    Args:
        document: A parsed JSON document

    Returns:
        The file paths, CORE types left out
    """
    if isinstance(document, dict):
        for key, value in document.items():
            if key == 'decision_logic_type' and isinstance(value, str):
                yield value
            elif key == 'worker_instances' and isinstance(value, dict):
                yield from (v for v in value.values() if isinstance(v, str))
            else:
                yield from _strategy_paths(value)
    elif isinstance(document, list):
        for item in document:
            yield from _strategy_paths(item)


class TestTheRuleAgainstWhatShippedRunsLoad:

    def test_every_strategy_file_a_shipped_set_or_profile_names_is_executable(self):
        named: List[str] = []
        for folder in ('configs', 'tests/fixtures'):
            for file in sorted((_ROOT / folder).rglob('*.json')):
                try:
                    document = json.loads(file.read_text(encoding='utf-8'))
                except ValueError:
                    continue
                named += [path for path in _strategy_paths(document)
                          if not path.startswith('CORE/')]
        assert named, 'no shipped configuration names a strategy file — the walk found nothing'
        assert [path for path in named if not is_executable_path(path)] == []


class TestTheEnding:

    def test_every_ending_says_whether_it_is_intended(self):
        intended = {ending for ending in ComparisonEnding if ending.is_intended()}
        assert intended == {ComparisonEnding.NO_EXECUTABLE_CHANGE, ComparisonEnding.COMPARED}
        assert set(ComparisonEnding) - intended == {ComparisonEnding.REFUSED,
                                                    ComparisonEnding.STAND_FAILED}
