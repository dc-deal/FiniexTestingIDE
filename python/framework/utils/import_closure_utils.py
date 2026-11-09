"""
The import closure of a code path, and whether two versions of a file compute the same thing.

The closure is every project file a set of entry points reaches through its `python.*` imports,
followed statically through the syntax tree — no module is imported, so the same function answers
for the working tree and for any commit, by the `read` it is handed. An import under a condition
counts like any other: a path that may run is a path.

A file whose only change is a docstring computes what it computed before: the syntax tree is
compared without docstrings, and a comment is never part of it in the first place.
"""

import ast
import hashlib
from typing import Callable, Iterable, List, Optional, Set

# The project's own top-level package — the only imports the closure follows
_PROJECT_PACKAGE = 'python'

# The syntax-tree nodes whose body may open with a docstring
_DOCSTRING_HOLDERS = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)


def import_closure(entry_points: Iterable[str],
                   read: Callable[[str], Optional[str]],
                   excluded_prefixes: Iterable[str] = ()) -> List[str]:
    """
    Every project file the entry points reach through their imports, the entry points included.

    `from python.a import b` reaches `python/a.py`, and `python/a/b.py` where `b` is a module. A
    file under an excluded prefix is neither part of the closure nor followed further.

    Args:
        entry_points: Repository-relative paths of the files the path starts at
        read: Path → its source text; None where no such file exists
        excluded_prefixes: Repository-relative path prefixes left out of the closure

    Returns:
        The closure's repository-relative paths, sorted
    """
    excluded = tuple(excluded_prefixes)
    reached: Set[str] = set()
    to_visit = [path for path in entry_points if not path.startswith(excluded)]
    while to_visit:
        path = to_visit.pop()
        if path in reached:
            continue
        source = read(path)
        if source is None:
            continue
        reached.add(path)
        for imported in _imported_files(ast.parse(source, filename=path)):
            if imported not in reached and not imported.startswith(excluded):
                to_visit.append(imported)
    return sorted(reached)


def same_syntax_tree(old_source: str, new_source: str) -> bool:
    """
    Whether two versions of a file differ in nothing but docstrings, comments and layout.

    Args:
        old_source: One version's text
        new_source: The other version's text

    Returns:
        True when both parse to the same syntax tree once every docstring is removed
    """
    return _tree_without_docstrings(old_source) == _tree_without_docstrings(new_source)


def syntax_digest(source: str) -> str:
    """
    A digest of what a file computes: its syntax tree without docstrings.

    Two versions with the same digest differ in nothing but docstrings, comments and layout, so a
    declaration pinned to it holds through a corrected comment and breaks at the first changed
    line of code.

    Args:
        source: The file's text

    Returns:
        The SHA-256 of the tree's dump, as hex
    """
    return hashlib.sha256(_tree_without_docstrings(source).encode('utf-8')).hexdigest()


def _imported_files(tree: ast.AST) -> List[str]:
    """
    The repository-relative files a module's project imports may name.

    Args:
        tree: The module's syntax tree

    Returns:
        Candidate paths, one per module an import may mean; a candidate that is no file is
        dropped by the caller's read
    """
    modules: List[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.level == 0 and node.module is not None:
            if _is_project_module(node.module):
                modules.append(node.module)
                modules += [f'{node.module}.{alias.name}' for alias in node.names]
        elif isinstance(node, ast.Import):
            modules += [alias.name for alias in node.names if _is_project_module(alias.name)]
    return [module.replace('.', '/') + '.py' for module in modules]


def _is_project_module(module: str) -> bool:
    """
    Whether a dotted module name lies in the project's own package.

    Args:
        module: The dotted name an import statement carries

    Returns:
        True for the package itself and everything under it
    """
    return module == _PROJECT_PACKAGE or module.startswith(f'{_PROJECT_PACKAGE}.')


def _tree_without_docstrings(source: str) -> str:
    """
    A file's syntax tree with every docstring removed, rendered for comparison.

    Args:
        source: The file's text

    Returns:
        The tree's dump without line and column positions
    """
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, _DOCSTRING_HOLDERS) and _opens_with_docstring(node.body):
            node.body = node.body[1:]
    return ast.dump(tree, include_attributes=False)


def _opens_with_docstring(body: List[ast.stmt]) -> bool:
    """
    Whether a body's first statement is a bare string — its docstring.

    Args:
        body: A module's, class's or function's statements

    Returns:
        True when the first statement is a string expression
    """
    return (bool(body) and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant) and isinstance(body[0].value.value, str))
