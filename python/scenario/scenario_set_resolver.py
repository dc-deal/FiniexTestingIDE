"""
FiniexTestingIDE - Scenario Set Resolver (#538)

Where a scenario set file lives, asked by name.

This existed twice, character for character including its docstring — once in
`ScenarioSetFinder` and once in `ScenarioConfigLoader` (§19). Both walked `user_algos/`
recursively on every call, and the measurement is why this module exists at all: listing
seventeen scenario sets cost **19.5 s**, of which **11.6 s** was 79 recursive globs over a tree of
107 directories, against **0.063 s** spent reading the JSON. On this tree a directory walk costs
282x what it costs on a local disk (§42), so the answer is not a faster walk but no walk.

**The store is an accelerator, never the only way to find anything.** A name it knows is answered
from the index plus one `stat` on the recorded path — 5.5 ms against 613 ms for a single glob. A
name it does not know falls through to the search that was always there, and the successful search
REGISTERS what it found, so the next call is fast. That ordering matters: a store that had to be
populated before the app worked would be a store that can lock the app out.
"""

from pathlib import Path
from typing import List, Optional

from python.framework.store.run_config_store import RunConfigStore
from python.framework.types.run_config_types import RunConfigKind


def resolve_scenario_set_path(
    filename: str,
    user_config_path: Path,
    user_algo_dirs: List[Path],
    config_path: Path,
    store: Optional[RunConfigStore] = None,
) -> Path:
    """
    Resolve a scenario set file path.

    A filename that is already a usable path is used directly. Otherwise the order is
    user_configs -> the run-config store -> user_algo_dirs (recursive) -> configs.

    Args:
        filename: Full path or config filename (e.g. "eurusd_3_windows.json")
        user_config_path: The user's own scenario-set directory
        user_algo_dirs: The algo workspaces to search recursively, as a last resort
        config_path: The project's scenario-set directory, and the fallback answer
        store: The run-config store, or None to skip the accelerated path entirely

    Returns:
        Resolved Path
    """
    direct = Path(filename)
    if direct.exists():
        return direct

    user_path = user_config_path / filename
    if user_path.exists():
        return user_path

    # The accelerated path. `resolve` confirms with a stat and answers None when the file moved,
    # so a stale entry costs one stat and then falls through rather than returning a wrong path.
    if store is not None:
        known = store.resolve(filename)
        if known is not None:
            return known

    for algo_dir in user_algo_dirs:
        if not algo_dir.exists():
            continue
        for p in algo_dir.rglob(filename):
            # Warm the index with what the walk cost, so the next caller does not pay it again.
            if store is not None:
                store.register(p, RunConfigKind.SCENARIO_SET)
            return p

    return config_path / filename
