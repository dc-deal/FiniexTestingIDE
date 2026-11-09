"""
The import closure of a code path, and the syntax-tree comparison the divergence gate holds it by
(#512).

The closure is computed from source text alone, so a synthetic tree of a few files shows every
way an import reaches a file; one test over this working tree holds the real path's shape. The
comparison is shown on the edits a reviewer would call harmless and on the smallest one that is
not.
"""

from pathlib import Path
from typing import Dict

from python.framework.constants.divergence_gate_constants import (
    GATE_ENTRY_POINTS,
    GATE_EXCLUDED_PREFIXES,
)
from python.framework.utils.import_closure_utils import (
    import_closure,
    same_syntax_tree,
    syntax_digest,
)
from python.framework.validators.divergence_gate_validator import judge_code_path

_ROOT = Path(__file__).resolve().parents[3]

_TREE: Dict[str, str] = {
    'python/app/entry.py': (
        'from python.app.helpers import helper\n'
        'from python.app import named_module\n'
        'import python.app.plain\n'
        'from python.app.missing import ghost\n'
        'from .relative import nothing\n'
        'import json\n'
        'if False:\n'
        '    from python.app.conditional import later\n'),
    'python/app/helpers.py': 'from python.app.api.served import Model\nhelper = 1\n',
    'python/app/named_module.py': 'from python.app.helpers import helper\n',
    'python/app/plain.py': 'from python.app.deep.leaf import LEAF\n',
    'python/app/deep/leaf.py': 'LEAF = 1\n',
    'python/app/conditional.py': 'later = 1\n',
    'python/app/api/served.py': 'from python.app.only_served import X\nModel = 1\n',
    'python/app/only_served.py': 'X = 1\n',
    'python/app/unreached.py': 'Y = 1\n',
}


def _closure(**options) -> list:
    return import_closure(['python/app/entry.py'], _TREE.get, **options)


class TestTheClosure:

    def test_it_follows_every_way_an_import_reaches_a_file(self):
        reached = _closure()
        assert {'python/app/helpers.py', 'python/app/named_module.py', 'python/app/plain.py',
                'python/app/deep/leaf.py', 'python/app/conditional.py'} <= set(reached)

    def test_it_holds_its_entry_points_and_nothing_unreached(self):
        reached = _closure()
        assert 'python/app/entry.py' in reached
        assert 'python/app/unreached.py' not in reached
        assert reached == sorted(reached)

    def test_an_import_naming_no_file_and_a_relative_one_add_nothing(self):
        assert all(path in _TREE for path in _closure())

    def test_an_excluded_prefix_is_neither_held_nor_followed(self):
        with_api = _closure()
        without = _closure(excluded_prefixes=('python/app/api/',))
        assert 'python/app/api/served.py' in with_api and 'python/app/only_served.py' in with_api
        assert not any(path.startswith('python/app/api/') for path in without)
        assert 'python/app/only_served.py' not in without

    def test_on_this_tree_it_holds_the_entry_points_and_nothing_it_excludes(self):
        tree = {str(path.relative_to(_ROOT)): path.read_text(encoding='utf-8')
                for path in (_ROOT / 'python').rglob('*.py') if '__pycache__' not in path.parts}
        verdicts = judge_code_path('none', tree, tree, {})
        paths = {verdict.path for verdict in verdicts}
        assert set(GATE_ENTRY_POINTS) <= paths
        assert not any(path.startswith(GATE_EXCLUDED_PREFIXES) for path in paths)
        assert all(verdict.change.value == 'unchanged' for verdict in verdicts)

    def test_on_this_tree_the_gate_lies_outside_the_path_it_judges(self):
        tree = {str(path.relative_to(_ROOT)): path.read_text(encoding='utf-8')
                for path in (_ROOT / 'python').rglob('*.py') if '__pycache__' not in path.parts}
        paths = {verdict.path for verdict in judge_code_path('none', tree, tree, {})}
        own = [path for path in tree if '/divergence' in path or 'import_closure' in path]
        assert own and not set(own) & paths


class TestTheSyntaxTree:

    _CODE = (
        '"""A module."""\n'
        'class Thing:\n'
        '    """A class."""\n'
        '    def method(self):\n'
        '        """A method."""\n'
        '        return 1  # one\n')

    def test_docstrings_comments_and_layout_do_not_count(self):
        edited = (
            '"""The module, described anew."""\n\n'
            'class Thing:\n'
            '    """The class."""\n\n'
            '    def method(self):\n'
            '        """What the method returns."""\n'
            '        # a comment of its own\n'
            '        return 1\n')
        assert same_syntax_tree(self._CODE, edited)
        assert syntax_digest(self._CODE) == syntax_digest(edited)

    def test_a_changed_value_counts(self):
        edited = self._CODE.replace('return 1', 'return 2')
        assert not same_syntax_tree(self._CODE, edited)
        assert syntax_digest(self._CODE) != syntax_digest(edited)

    def test_a_string_that_is_no_docstring_counts(self):
        edited = self._CODE.replace('return 1', "x = 'a'\n        return 1")
        assert not same_syntax_tree(self._CODE, edited)

    def test_the_digest_is_hex_of_a_fixed_length(self):
        digest = syntax_digest(self._CODE)
        assert len(digest) == 64 and int(digest, 16) >= 0
