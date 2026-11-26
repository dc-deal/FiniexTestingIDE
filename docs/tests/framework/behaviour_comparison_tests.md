# Behaviour Comparison Tests

The behaviour comparison runs one fixed backtest on two code stands and keeps nothing of either
(#641). Its failures are quiet ones — a stand that ran the wrong code, a scratch folder left behind,
a write that reached a real store — so the suite holds each step on throwaway repositories, and
once end to end on two real commits of this repository. It does not compare the runs' records yet:
that is a later part of the same issue.

Location: `tests/framework/behaviour_comparison/`

## What it covers

| File | What it holds |
|---|---|
| `test_changed_path_classifier.py` | The declared rule of what a run does not execute — documents and tests, fixtures excepted — held to every strategy file a shipped scenario set or profile names, and applied to the framework only: a strategy repository skips nothing; every ending of a comparison says whether it is intended |
| `test_behaviour_comparison_git.py` | A revision resolved to its full commit, the paths between two commits with a rename naming both sides, and a clone at a commit that is clean and leaves every file of the source's `.git` as it was |
| `test_stand_home_builder.py` | A stand built from a data home laid out in the test: the indexes and caches copied, the archive and the workspace linked, the strategy repository at its commit, a strategy repository outside the home and an unknown commit refused |
| `test_loaded_code.py` | Whether a stand ran on its own code, from code identities built in place — each case changes one thing the header could say; an identity that cannot say is never a match; the exit code of every ending |
| `test_tree_backtest_launch.py` | The one launch both code-stand comparisons start a backtest with, shared with the divergence report's A/A: working directory, import path, data home, the caller's variables, and the lines a failed run is quoted by |
| `test_behaviour_comparison_coordinator.py` | What is refused before anything is built — an unknown revision, a missing set, a strategy revision without a repository or on one stand only — and a change to documents and tests only, which ends the comparison before it builds |
| `test_behaviour_comparison_end_to_end.py` | One comparison of two real commits of this repository on a short fixture set: both stands ran on their own commits, and the scratch folder is gone |

## What the end-to-end case costs

It builds two clones of this repository and runs two backtests, once for the module. On this
machine a clone takes most of the time — the checkout reads the repository's loose objects one by
one across the mount — so the module runs in about half a minute. The newer commit is `HEAD`, the
older one the parent of the newest commit that changed `python/`, so there is always something
executable to run. A scratch `HOME` keeps the stands' backtests out of the machine's activity list.

## Running

```
python -m pytest tests/framework/behaviour_comparison -v
```

VS Code: `🧩 Pytest: Behaviour Comparison (All)`.
