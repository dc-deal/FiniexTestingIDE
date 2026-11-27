# Comparing Two Code Stands Before a Deploy

A commit says that code changed. It does not say whether what a strategy does changed — and that
is the question before a bot moves onto new code. To git, these look alike:

| The commit | What git shows | What the strategy does |
|---|---|---|
| A comment reworded in an indicator | one changed line in a `.py` file | nothing moves |
| A framework release that reorganises a module | hundreds of changed lines | nothing moves: the same decisions, the same orders, the money equal to the last digit |
| The oldest warmup bar read one percent too high | one changed line | every order's size moves, by less than a cent of balance |

The configuration does not see it either. The strategy's settings stay what they were, so
everything built from them — the rendered configuration, the composite key a run is grouped by —
stays the same while the behaviour underneath it has changed:

```
Monday     my_strategy   fast_period 12   composite 7f3a9c2e
Tuesday    a framework release moves its fills
           my_strategy   fast_period 12   composite 7f3a9c2e   ← another behaviour, the same key
```

Reading the change off the code is no way out: whether a change of code changes what a program
does cannot be decided in general. What can be done is to measure it for one fixed run. That is
what the behaviour comparison is — a benchmark of behaviour rather than of speed: the same fixed
backtest runs on the code that runs now and on the code that is to be deployed, each from its own
throwaway copy, over the same data, and nothing of either is kept. Whoever runs a bot can run it
for their own bot before a deploy.

This guide covers what is built so far: which paths changed, the two stands built and run with the
proof that each run loaded its own code, and the two runs' records held against each other with
what that says. Which changed lines no run reached, and the record a comparison leaves, are later
parts of the same work. Comparing a live bot with a backtest of its own window is a different
question, answered by [Comparing a Live Bot With Its Backtest](divergence_report_guide.md).

## A stand is two commits

A **stand** is a framework commit and, where a strategy lives in a repository of its own, a
strategy commit. Old is what runs now; new is what is to be deployed. Either side may move, both
may move, and a strategy kept outside this repository comes along without its code entering it:

```
python python/cli/behaviour_comparison_cli.py compare \
    --scenario-set configs/scenario_sets/backtesting/my_check_set.json \
    --old-framework v1.5.0 --new-framework dev-1-6-0 \
    --strategy-repo user_algos --old-strategy 4327d40 --new-strategy my-branch
```

A revision may be a branch, a tag or an abbreviation; the comparison works on the full commit it
names. The framework repository is this tree's unless `--framework-repo` names another. A strategy
revision needs `--strategy-repo`, and both stands name one or neither does. The scenario set is the
same file for both stands: choose a short one that passes through what the strategy does.

## What happens, step by step

```
1 paths   framework: 61 changed, 37 executable, 1 comment only · strategy: 2 changed, 2 executable
2 build   old and new in 28.6 s
3 run     old  ✓ run 20261124_190601_ab12cd34 · loaded framework 1a2b3c4d5e6f · 4 s
          new  ✓ run 20261124_190712_ef56ab78 · loaded framework 5e6f7a8b9c0d · 4 s
4 compare the two runs' records, kind by kind, value by value
          a value differs, so the new stand ran again: new  ✓ run 20261124_190801_0a1b2c3d · …
6 result  DIFFERENT  orders
                     orders[*].requested_lots ×3 — first orders[0].requested_lots 1.0 → 0.5
          RESHAPED   trades: a field only the new stand writes — trades[*].fee_basis
          IDENTICAL  order events · resting orders · booking periods · warnings and errors · …
          UNSEEN     signal data: none in either run — says nothing about a signal
          ended in result: the behaviour differs — read the cause before the deploy
7 clean   scratch removed
```

1. **Paths.** The changed paths of each repository between the two stands. A change confined to
   documents and tests cannot move what a run does, so the comparison ends here and builds nothing
   (`ended in paths: no executable path changed — nothing to run`). The rule errs towards running:
   a path it does not name counts as executable, and so does everything below `tests/fixtures/`,
   because shipped scenario sets load their strategies from there. It is the framework's rule: a
   strategy repository's layout is its author's, so every change there runs. In either repository,
   a Python file whose syntax tree is unchanged — a change to comments, blank lines or formatting —
   counts as not executed; a changed docstring is code, because a program can read it.
2. **Build.** Each stand gets a folder of its own below the system's temporary directory: a clone
   of the framework at its commit, and beside it a scratch data home holding the strategy
   repository at its commit, where the scenario set's strategy paths expect it. What a run may
   rewrite — the tick and bar indexes, the discovery caches, the signal store, the broker cache —
   is copied from the data home this command runs on; the tick archive and the workspace are linked,
   because a backtest only reads them. The clones borrow the source repository's objects instead of
   copying them, so nothing is written into its `.git`.
3. **Run.** Old first, then new, each as a process of its own from its own tree, against its own
   scratch home. A stand counts as run only where its run's header proves which code it loaded:
   the framework from its own tree at its commit, every strategy file from that tree or from the
   stand's strategy repository at its commit, nothing uncommitted. A header that cannot say is a
   failure, never a match.
4. **Compare.** Every record a backtest writes that says what the run did — the order events, the
   orders, the trades, the resting orders, the positions and the account, the booking periods, the
   fills, what the run logged and read — is held against the other stand's, value by value and to
   the last digit. What says how fast this machine was, the run's provenance, the logs and the CSV
   copies are left out, each for a stated reason. So are a few fields that differ between two runs
   of the same code: the run's id, and stamps taken from the wall clock. Where a value differs, the
   new stand runs once more: a value its two runs disagree on is not a change of behaviour but code
   that does not reproduce itself, and it is shown as such.
6. **Result.** One line per kind of record:

   | Verdict | Means |
   |---|---|
   | `IDENTICAL` | both stands wrote the same values |
   | `DIFFERENT` | a value both write differs, and the new stand's second run agrees — every differing field is named with how often it differs and its first two values |
   | `UNSTABLE` | the new stand's two runs disagree — the new code does not reproduce itself there |
   | `RESHAPED` | no value differs, but the record's shape does: a field, a kind of record or a schema version only one stand writes |
   | `UNSEEN` | what this comparison cannot say: a kind of record empty in both runs |

   The whole takes the weightiest line: `DIFFERENT` over `UNSTABLE` over `RESHAPED` over
   `IDENTICAL`; `UNSEEN` never decides it. The result decides nothing by itself: `IDENTICAL` says
   nothing on this run speaks against the deploy, `DIFFERENT` and `UNSTABLE` say the cause is read
   first, and `UNSEEN` says where to look with other means.
7. **Clean.** The folder is removed whatever happened before it, a failure or an interruption too.

## What it touches outside its folder

Nothing of the data home it runs on: every store a stand writes — its run, the ledger, the run
configurations, the logs — lies in its scratch home. Two writes leave the folder by design. Each
stand's backtest appears in the machine's activity list while it runs, as every backtest does, and
is removed when it ends. And each clone reads its objects from the source repository for as long
as it exists: a commit amended away and then pruned by the source's own maintenance while a
comparison runs would leave the clone unreadable. A comparison lasts minutes, so the case is
named here rather than guarded against.

Building a stand costs most of its time on this kind of machine: a checkout reads each object of
the source repository as a file, so a repository with many loose objects takes longer to clone
than its size suggests.

## Exit codes

`0` nothing executable changed, or the result is `IDENTICAL` or `RESHAPED` · `3` the result is
`DIFFERENT` or `UNSTABLE` · `2` refused, or a stand could not be built, ran no backtest or loaded other code ·
`1` an error · `130` interrupted. A stand whose own backtest finished with logged errors counts as
run: it computed its events, and its records are compared.

## Related

- [Comparing a Live Bot With Its Backtest](divergence_report_guide.md) — a live deployment held
  against a backtest of its windows; its `aa` holds one window on two code stands, and both start
  their stands' backtests the same way
- [Changing a Live Bot](changing_a_live_bot_guide.md) — what a change of a running bot's
  configuration does to its record and its series
- [Behaviour Comparison Tests](../tests/framework/behaviour_comparison_tests.md)
