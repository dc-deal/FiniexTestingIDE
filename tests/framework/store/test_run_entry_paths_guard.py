"""
Where a run's files lie is asked of the run store's declaration (#621): no module builds a run
file's path from the layout constants or from a file name of its own. The rules, each over the
production code:

- a constant whose value is a part of a declared member's path is imported only by its sources —
  the constants module, the two declarations and the report section specs — and by the modules
  listed below with their reasons; the list may only shrink, so an entry a module no longer needs
  fails too;
- such a file name never stands as a literal outside the constants module;
- a path is never joined from a section spec's file name outside the section writer.

The one-time scripts in python/experiments are not scanned: each runs once against the layout of
its day. The venue probes and the re-import tool are, because they run again.
"""

import ast
from pathlib import Path
from typing import Dict, List, Set

import pytest

import python.framework.constants.run_layout_constants as run_layout_constants
from python.framework.store.entry_members.run_entry_members import RUN_ENTRY_MEMBERS
from python.framework.store.entry_members.sweep_entry_members import SWEEP_ENTRY_MEMBERS

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_SCANNED = ('python/api', 'python/cli', 'python/configuration', 'python/data_management',
            'python/framework', 'python/scenario', 'python/system',
            'python/experiments/venue_probes', 'python/experiments/raw_archive_reimport')
_LAYOUT_MODULE = 'python.framework.constants.run_layout_constants'
_LAYOUT_FILE = 'python/framework/constants/run_layout_constants.py'

# Where the names come from: they may import them
_SOURCES = {
    _LAYOUT_FILE,
    'python/framework/store/entry_members/run_entry_members.py',
    'python/framework/store/entry_members/sweep_entry_members.py',
    'python/framework/reporting/io/artifact_specs.py',
}

# Modules that still import such a name, each with why. The list only shrinks.
_STILL_IMPORTING = {
    'python/framework/autotrader/session_log_retention.py':
        'its deletion rule keeps a narrower name pattern than the declared day log — eight digits '
        'where the declaration takes any plain name — and its tests hold the two together',
}

# The section writer joins a section's folder with the spec's file name; the run entry writer
# hands it the folder the declaration names
_SPEC_JOINERS = {'python/framework/reporting/io/report_artifact_io.py'}


def _segments() -> Set[str]:
    """
    Every part of every declared member path, run and sweep alike.

    Returns:
        The parts
    """
    return {segment for member in RUN_ENTRY_MEMBERS + SWEEP_ENTRY_MEMBERS
            for segment in member.path_pattern.split('/')}


@pytest.fixture(scope='module')
def modules() -> Dict[str, ast.AST]:
    """
    Every scanned module, parsed once.

    Returns:
        Its syntax tree by its path relative to the project root
    """
    parsed = {}
    for root in _SCANNED:
        for path in sorted((_PROJECT_ROOT / root).rglob('*.py')):
            parsed[path.relative_to(_PROJECT_ROOT).as_posix()] = ast.parse(
                path.read_text(encoding='utf-8'))
    return parsed


@pytest.fixture(scope='module')
def guarded() -> Set[str]:
    """
    The layout constants whose value is a part of a declared member's path.

    Returns:
        Their names
    """
    segments = _segments()
    return {name for name, value in vars(run_layout_constants).items()
            if name.isupper() and isinstance(value, str) and value in segments}


def _importers(modules: Dict[str, ast.AST], guarded: Set[str]) -> Dict[str, List[str]]:
    """
    The modules that import a guarded name from the layout constants, or the module itself.

    Args:
        modules: The parsed modules
        guarded: The guarded names

    Returns:
        The names each imports, by module
    """
    found: Dict[str, List[str]] = {}
    for path, tree in modules.items():
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == _LAYOUT_MODULE:
                names = [alias.name for alias in node.names if alias.name in guarded]
                if names:
                    found.setdefault(path, []).extend(names)
            if isinstance(node, ast.Import) and any(
                    alias.name == _LAYOUT_MODULE for alias in node.names):
                found.setdefault(path, []).append('the whole module')
    return found


def test_the_guard_watches_names(guarded):
    assert guarded, 'no layout constant names a part of a member path — the guard sees nothing'


def test_only_the_sources_import_a_member_file_name(modules, guarded):
    outside = {path: names for path, names in _importers(modules, guarded).items()
               if path not in _SOURCES and path not in _STILL_IMPORTING}

    assert not outside, (
        f'these modules build run file paths from the layout constants; ask the run entry '
        f'reader or writer for the member instead: {outside}')


def test_the_list_of_modules_still_importing_only_shrinks(modules, guarded):
    importers = _importers(modules, guarded)
    stale = [path for path in _STILL_IMPORTING if path not in importers]

    assert not stale, f'remove these from _STILL_IMPORTING — they no longer need it: {stale}'


def test_no_member_file_name_stands_as_a_literal(modules):
    file_names = {segment for segment in _segments() if '.' in segment and '{' not in segment}
    literals = [f'{path}:{node.lineno} {node.value!r}'
                for path, tree in modules.items() if path != _LAYOUT_FILE
                for node in ast.walk(tree)
                if isinstance(node, ast.Constant) and node.value in file_names]

    assert not literals, f'a run file named as a literal: {literals}'


def test_no_path_is_joined_from_a_section_spec_name(modules):
    joins = [f'{path}:{node.lineno}'
             for path, tree in modules.items() if path not in _SPEC_JOINERS
             for node in ast.walk(tree)
             if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Div)
             and any(isinstance(side, ast.Attribute) and side.attr == 'filename'
                     for side in (node.left, node.right))]

    assert not joins, f'a path joined from a section spec name: {joins}'
