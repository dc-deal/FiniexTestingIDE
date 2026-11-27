# Behaviour Comparison Tests

The behaviour comparison runs one fixed backtest on two code stands and keeps nothing of either
(#641), then holds the two runs' records against each other. Its failures are quiet ones — a stand
that ran the wrong code, a scratch folder left behind, a write that reached a real store, a record
left out of the comparison, a stamp of the wall clock read as a change of behaviour — so the suite
holds each step on throwaway repositories and on records written in the test, and end to end on a
clone of this repository with a neutral and a defective commit of its own. Which changed lines no
run reached is a later part of the same issue.

Location: `tests/framework/behaviour_comparison/`

## What it covers

| File | What it holds |
|---|---|
| `test_changed_path_classifier.py` | The declared rule of what a run does not execute — documents and tests, fixtures excepted — held to every strategy file a shipped scenario set or profile names, and applied to the framework only: a strategy repository skips nothing; a Python file whose syntax tree is unchanged executes nothing in either repository, a docstring or an expression does; every ending of a comparison says whether it is intended |
| `test_behaviour_comparison_git.py` | A revision resolved to its full commit, the paths between two commits with a rename naming both sides, a clone at a commit that is clean and leaves every file of the source's `.git` as it was, and files read as given commits hold them in one read |
| `test_compared_records.py` | Every record a backtest writes is compared or left out with its reason, and nothing else is named; no log is compared |
| `test_run_record_comparison.py` | Two runs' records held against each other on run directories written in the test: a value named by its path with both sides, an extra record, a changed type, a field only one run writes, a schema version, the ignored fields, an order event stream put in scenario and `seq` order, a member whose name has a variable part |
| `test_comparison_result.py` | The verdict of each kind of record and of the whole — identical, different and counted by field, unstable where the new stand's two runs disagree, reshaped, unseen, both lines where a value and the shape moved — the exit code of each, and the lines the console prints |
| `test_stand_home_builder.py` | A stand built from a data home laid out in the test: the indexes and caches copied, the archive and the workspace linked, the strategy repository at its commit, a strategy repository outside the home and an unknown commit refused |
| `test_loaded_code.py` | Whether a stand ran on its own code, from code identities built in place — each case changes one thing the header could say; an identity that cannot say is never a match; the exit code of every ending |
| `test_tree_backtest_launch.py` | The one launch both code-stand comparisons start a backtest with, shared with the divergence report's A/A: working directory, import path, data home, the caller's variables, and the lines a failed run is quoted by |
| `test_behaviour_comparison_coordinator.py` | What is refused before anything is built — an unknown revision, a missing set, a strategy revision without a repository or on one stand only — and a change to documents and tests only, or to comments only, which ends the comparison before it builds |
| `test_behaviour_comparison_end_to_end.py` | Two comparisons on a clone of this repository with two commits of its own in the probe the fixture set runs: a neutral one comes out identical, a defect that halves every lot comes out different and names the lots, the new stand's second run agrees with its first, both stands ran on their own commits, and nothing is left behind |

## What the end-to-end case costs

It makes a clone of this repository without a checkout and two commits on it, then builds four
stand trees and runs five backtests — the defect's new stand runs twice. On this machine the stand
trees take most of the time — a checkout reads the repository's loose objects one by one across the
mount — so the module runs in about a minute. A scratch `HOME` keeps the stands' backtests out of the
machine's activity list.

## Running

```
python -m pytest tests/framework/behaviour_comparison -v
```

VS Code: `🧩 Pytest: Behaviour Comparison (All)`.
