"""
Grid expander (#390) — the v0 combination generator.

Pure Cartesian product of a parameter grid → a deterministic list of combinations.
Keys are sorted and value order is preserved, so the same grid always expands to the
same ordered combinations (determinism, pairs with #368). This is the pluggable
"combination generator" seam: #32 adds random / Bayesian generators with the same
`grid → List[combo]` contract.
"""

import itertools
from typing import Any, Dict, List


def expand_grid(grid: Dict[str, List[Any]]) -> List[Dict[str, Any]]:
    """
    Expand a parameter grid into its Cartesian product of combinations.

    Args:
        grid: Map of dotted parameter path → list of candidate values

    Returns:
        Ordered list of combinations; each combination maps every path to one value
        (an empty grid yields a single empty combination = the base config)
    """
    paths = sorted(grid.keys())
    value_lists = [grid[path] for path in paths]
    return [dict(zip(paths, values)) for values in itertools.product(*value_lists)]
