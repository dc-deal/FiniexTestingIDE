"""
Fingerprint every arithmetic expression by its SHAPE, so the same formula written twice
under different names shows up as one shape in two files.

The case it was built from: `SpreadFee.calculate_cost` computed
`(ask - bid) * 10**digits * tick_value * lots` while `gross_pnl_from_price_diff` computed
`price_diff * 10**digits * tick_value * lots` — the same conversion of the same quantity,
in two modules, under different names. Nothing could see it: the identifiers differ, so a
text search finds nothing, and the two lines are far below any clone detector's minimum
token count. Finding it was luck, and the cost of not finding it was that the spread was
charged twice for eleven months.

Normalization: every identifier, attribute, subscript and call collapses to `_`; numeric
literals are KEPT. Keeping them is the point — `10 ** digits` is what makes a points
conversion recognizable as one, and a shape with the literal erased would match every
other power.

In clone-detection terms this is a Type-2 clone (same structure, different identifiers),
which is the tractable class. Type-4 — same meaning, different structure — is undecidable
in general and is not attempted.
"""

import ast
from collections import defaultdict
from pathlib import Path
from typing import Dict, List, Optional, Sequence, Tuple

# The binary operators worth fingerprinting. Bit operators are deliberately absent: they
# appear in flag arithmetic, where a shared shape says nothing about a shared formula.
_OPERATORS = {
    ast.Add: '+',
    ast.Sub: '-',
    ast.Mult: '*',
    ast.Div: '/',
    ast.Pow: '**',
    ast.Mod: '%',
    ast.FloorDiv: '//',
}

# A shape needs this many operators before it can carry a formula's identity. TWO, not three,
# and that is not a guess: at three the scanner does not find the case it was built from —
# `_*(10**_)` has exactly two. A threshold that excludes the founding example is the wrong
# threshold, and `test_it_finds_the_case_it_was_built_from` is what holds this honest.
_MIN_OPERATORS = 2

# ...and this many DISTINCT kinds of them. One kind is a chain, not a formula: `a+b+c+d`
# is a sum, and every sum in the project would otherwise match every other one. Measured
# 2026-09-20: this rule alone removes the two noisiest shapes and keeps every real finding.
_MIN_DISTINCT_OPERATORS = 2


def normalize(node: ast.AST) -> Optional[str]:
    """
    Reduce one expression to its shape.

    Args:
        node: The AST node to normalize

    Returns:
        The shape string, or None where the expression holds something that is not
        arithmetic (a comparison, a string concatenation, a call we cannot see into)
    """
    if isinstance(node, ast.BinOp):
        operator = _OPERATORS.get(type(node.op))
        if operator is None:
            return None
        left = normalize(node.left)
        right = normalize(node.right)
        if left is None or right is None:
            return None
        return f'({left}{operator}{right})'

    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        operand = normalize(node.operand)
        return f'(-{operand})' if operand is not None else None

    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float)):
        return str(node.value)

    if isinstance(node, (ast.Name, ast.Attribute, ast.Subscript, ast.Call)):
        return '_'

    return None


def is_significant(shape: str) -> bool:
    """
    Decide whether a shape is specific enough to mean anything.

    Args:
        shape: A normalized shape string

    Returns:
        True when it carries enough structure to identify a formula
    """
    # '**' and '//' contain '*' and '/', so counting on the raw string double-counts every
    # power and every floor division. Substituting the two-character operators first is what
    # makes both the total and the kind count honest.
    reduced = shape.replace('**', '^').replace('//', '\\')
    kinds = {symbol for symbol in '+-*/%^\\' if symbol in reduced}
    total = sum(reduced.count(symbol) for symbol in '+-*/%^\\')
    return total >= _MIN_OPERATORS and len(kinds) >= _MIN_DISTINCT_OPERATORS


# Where a formula is DECLARED to live exactly once. Only modules whose PURPOSE is
# mathematics belong here: `time_utils` and `market_calendar` are single-source too (§9, §37)
# but their arithmetic is incidental — a percentage, a millisecond conversion — and it collides
# with every unrelated percentage in the project. Measured 2026-09-20: including them turned 4
# findings into 7, and all three additions were noise.
SINGLE_SOURCE_ROOTS = ('python/framework/utils/trading_math/',)

# A test that independently re-derives a formula is doing its job — §46 pins the scalar and
# series forms against each other exactly that way — so a test file is never a leak.
_EXEMPT_ROOTS = ('tests/',)


def scan(project_root: Path, subdirs: Sequence[str]) -> Dict[str, List[Tuple[str, int]]]:
    """
    Fingerprint every arithmetic expression under the given directories.

    Paths come back RELATIVE to the project root, because every classification below asks
    which MODULE a site belongs to — and an absolute path silently fails that question on
    another machine, or when the tree moves.

    Args:
        project_root: The repository root every reported path is relative to
        subdirs: Directory names under it to walk

    Returns:
        Shape → the (relative path, line number) pairs where it occurs
    """
    found: Dict[str, List[Tuple[str, int]]] = defaultdict(list)
    for subdir in subdirs:
        root = project_root / subdir
        for path in sorted(root.rglob('*.py')):
            try:
                tree = ast.parse(path.read_text())
            except (SyntaxError, UnicodeDecodeError):
                # A file this project cannot parse is the undefined-name gate's problem,
                # not this one's — reporting it twice would only split the signal.
                continue
            for node in ast.walk(tree):
                if not isinstance(node, ast.BinOp):
                    continue
                shape = normalize(node)
                if shape is not None and is_significant(shape):
                    found[shape].append(
                        (path.relative_to(project_root).as_posix(), node.lineno))
    return found


def shapes_in_several_files(
    found: Dict[str, List[Tuple[str, int]]]
) -> Dict[str, List[Tuple[str, int]]]:
    """
    Keep only the shapes that occur in more than one file.

    A shape repeated inside one function is a loop or an unrolled step; the same shape in
    two files is a formula living in two places, which is what §19 is about.

    Args:
        found: The full scan result

    Returns:
        The subset occurring in two or more distinct files
    """
    return {
        shape: sites for shape, sites in found.items()
        if len({site[0] for site in sites}) >= 2
    }


def single_source_leaks(
    found: Dict[str, List[Tuple[str, int]]]
) -> Dict[str, Tuple[List[str], List[str]]]:
    """
    Find formulas that live in a single-source module AND somewhere else.

    This is the narrow question, and narrow is the point: "the same shape occurs in two
    files" reports 25 shapes here, one of which is a percentage appearing in 26 files. A
    gate that starts red on noise is a gate that gets switched off (§40).

    Args:
        found: The full scan result

    Returns:
        Shape → (the single-source files holding it, the non-test files outside them)
    """
    leaks: Dict[str, Tuple[List[str], List[str]]] = {}
    for shape, sites in found.items():
        paths = {site[0] for site in sites}
        inside = sorted(p for p in paths if p.startswith(SINGLE_SOURCE_ROOTS))
        outside = sorted(
            p for p in paths
            if not p.startswith(SINGLE_SOURCE_ROOTS) and not p.startswith(_EXEMPT_ROOTS)
        )
        if inside and outside:
            leaks[shape] = (inside, outside)
    return leaks
