# Comparing a Live Bot With Its Backtest

A strategy that was profitable in a backtest is worth trading only if the backtest predicts what
the bot does with real money. That question has an answer only after the fact: the live run's own
records, held against a backtest of exactly the same window, day by day and order by order. And it
is answered on a different machine from the one that trades. The live server should do nothing but
trade; the backtest needs the tick archive, the bars and the processor time.

This guide shows how a deployment's records reach the machine that compares, how to prove that
machine computes what the live one did, how to run the comparison and how to read its result.

Not here: how a bot is taken live and kept running — see
[Unattended Operation](../autotrader/unattended_operation.md) and the
[Deployment Ledger Guide](deployment_ledger_guide.md). What each option of the two commands does
in full is in the [CLI Tools Guide](../cli_tools_guide.md), section *Divergence Report*.

---

## The recommended setup: two systems, one version

**We recommend it explicitly:** a live system that trades and a backtest system that researches
and compares, both running FiniexTestingIDE checked out at the same release tag.

```
 live system (a server)                        backtest system (a workstation)
 ──────────────────────                        ────────────────────────────────
 FiniexTestingIDE at the release tag            FiniexTestingIDE at the release tag
 data home: the run tree, the results    ─────► a copy of that data home
 ledger, the run configurations          copy   the tick archive, kept current by the import
                                                divergence_report_cli.py  aa · compare
```

The split keeps the live machine small and undisturbed, and puts the archive and the processor
time where the backtests run. The single version is what makes the comparison mean something. A
backtest on other code measures the change of code, not the difference between live and backtest.
The comparison therefore refuses a backtest whose code differs from the session's release, file by
file over everything the backtest imports, unless that release declares the change.

The comparing system may run a later release of the same line, for example to use a comparison
that did not exist yet when the bot started. It then has to prove first that it computes exactly
what the session's release computes: that is the [A/A](#prove-the-instrument-first-aa) below.

## Copy the live system's data home

The comparison reads four things the live system writes into its data home: the run index, the
results ledger, the run-configuration store and the run directories. Copy the data home as a
whole, laid out as the live system laid it out:

```
rsync -a <live host>:<its data home>/ ./copies/2026-12-24/
```

The copy is read with `--records <copy>`. Each store is found in it by its own index file. The
run index names each run directory by the live system's path, so every one is moved into the copy
by the longest end of its path that exists there. A copy that holds two data homes is refused.
Copy only after the last trading day you compare has been sealed: the session writes its day at
the boundary, not before.

## A data home on the comparing system

Run both commands from the code tree, against a data home of their own. The data home holds the
strategy directories, the workspace and the data:

```
FINIEX_DATA_HOME=<home> PYTHONPATH=<tree>:<home> \
    python python/cli/divergence_report_cli.py …
```

The strategy is imported from the data home, which is why it stands on `PYTHONPATH`. The data home
must hold **no `python/` directory**. The code's packages carry no `__init__.py`, so Python merges
same-named directories across the import path: modules of another tree in the data home would mix
into the tree you run. Where the data home lives and what it holds is described in
[Data Storage Layout](../architecture/data_storage_layout.md), section *The data home — where
every root lives*.

## The archive must reach past the window

A backtest of a window needs ticks up to its end. The comparison checks that first, and where the
archive ends before the window does, it says so and waits:

```
NOT COMPARED: the archive ends 2026-12-22T23:59:58+00:00, not past the window's end
2026-12-24T00:00:00+00:00 — wait for the import that covers it
```

The archive grows with each import. If your ticks come from a collector that packages them on a
schedule, the newest days arrive with its next package, so plan the comparison after it.

Ticks the venue publishes as its own history are classified `external` and are refused by a
backtest unless a configuration admits them. A window has to lie in data your own collector
recorded. See [Data Import Pipeline](../data_pipeline/data_import_pipeline.md), section *A venue's
published history — external data*.

## Prove the instrument first: `aa`

An A/A backtests one window twice with the same inputs: once on the release the sessions ran, once
on the tree you compare with. Replay is deterministic, so the two must be identical, event by
event. A difference means the code computes something else, and every comparison on it would
measure that instead of the live run. Run it once whenever the comparing tree is not the session's
release, and before you trust a comparison.

Check out the session's release beside your tree:

```
git worktree add --detach ../release-1.5.0 v1.5.0
```

Then choose a window in which the strategy trades; a window without an order compares nothing:

```
python python/cli/divergence_report_cli.py aa --deployment <id> \
    --release-tree ../release-1.5.0 --window 2026-08-15,2026-11-06 \
    --records ./copies/2026-12-24
```

`--records` names the copy of the live system's data home, as for `compare`: the A/A reads the
deployment's sessions from it, and without it the comparing system's own stores name no such
deployment.

```
A/A 20261110_200256_2a3b4c5d · deployment <id> · ETHUSD · release 33989482 against this tree a1be0c79
WINDOW 1 · 2026-08-15 00:00:00 → 2026-11-06 00:00:00 · IDENTICAL
  release: run … · code 33989482 · broker … · 63 order event(s), 21 fill(s), 15 trade(s)
  current: run … · code a1be0c79 · broker … · 63 order event(s), 21 fill(s), 15 trade(s)
  Bars: 8 file(s) · unchanged
```

A window is IDENTICAL only where both runs read the same inputs on the code they claim, and traded:
- the release's run on the sessions' own commit;
- your tree on a commit with no uncommitted changes;
- one broker document on both sides;
- no bar file rendered between the two runs;
- at least one fill on either run — a window in which nothing traded proves no order path.

Where any of that cannot be shown, the window is *not checked*, with why, and the exit code is 2.
Measured 2026-11-10: one twelve-week window takes about two minutes per run.

## Compare: `compare`

```
python python/cli/divergence_report_cli.py compare --deployment <id> --decision-timeframe D1 \
    --records ./copies/2026-12-24 --until 2026-12-24T00:00:00Z
```

The command reads the live side, cuts it into segments where the code changed, and for each
segment:
1. builds a backtest of exactly its window from what the sessions recorded — their warmup, their
   frozen broker configuration, their rendered strategy;
2. runs that backtest, and has the gate check it is comparable;
3. measures each day's decision, each order and the account values;
4. asks the venue's own record where a difference may come from the data rather than the code;
5. judges every measure against bounds fixed before the run.

`--decision-timeframe` names the bar whose close is the strategy's daily decision; no strategy
declares it, so the comparison is told. A stop you made by hand is declared with
`--declared-stop <instant>,<changed|unchanged>[,<note>]`, so the comparison knows whether the
account was changed in it.

**The comparison never decides on the wall clock.** A record carries two kinds of time. The time
of its events — a tick, a bar's close, a decision, a fill — is what the comparison measures. The
stamps that say when something was fetched, written, exported or copied are the clock of the
machine that did it: they tell where a record came from, not what it holds, so they never make two
records count as different. A session that restarts fetches the venue's configuration again and
freezes a document of its own. The gate compares that document with the one the backtest trades
with by what it trades with — the traded symbol's specification and everything else, the fetch and
export stamps left out:

```
  Gate: comparable · release 1.5.0 · session 33989482 · backtest a1be0c79
    warning divergence_gate_broker_config: 2 sessions each froze a broker configuration of their
      own, and the documents differ only in when they were fetched (… fetched 2026-10-30T13:01:27Z,
      … fetched 2026-11-10T00:05:01Z): the backtest trades with what every one of them traded with.
```

A changed fee or lot size still refuses, and the refusal names where the documents differ. A stamp
is read on purpose in one place only, and there it can only refuse: the A/A is *not checked* where
a bar file was rendered between its two runs, because a new render may be a new input.

**The gate pins a file's syntax tree under one Python version.** A changed file the release
declares is held to the tree it was declared with, rendered by the gate's own code without its
docstrings. The pins record the interpreter they were taken under; under another one the gate
refuses once, naming both versions, rather than reading every pinned file as changed.

**The backtest's warmup is checked before any decision is.** The backtest starts from the warmup
the session recorded, and records what it injected under its scenario's name. Before measure 1 the
two are held to each other bar by bar; a difference, or a record missing on one side, leaves
measure 1 *not checked*. Beside it, each session's recorded warmup — the first one's, and the new
one's after every restart — is held to the archive's bars of the same periods. That line decides
nothing: the session warmed up on the venue's bars, the archive renders its own from the
collector's ticks, and the line shows how far the two stand apart. A bar differs where a price
does; the volume is shown apart, as the recorded volume over the archived one, because the venue
counts every trade and the collector only those it received:

```
  Warmup injected: EQUAL — every bar as the session recorded it (D1 60 bars), complete at …
  Warmup against the archive: <session> D1 · 60 compared · 1 differ in price · 0 not in the archive · volume recorded ÷ archived 1.0042
    2026-10-03T00:00:00+00:00: high 2611.4 recorded, 2609.8 archived
```

## Reading the result

Each measure ends *passed*, *failed* or *not checked*, always with its reason. **Passed means no
divergence beyond the bounds over the compared days — never that parity is proven.** Thirty days
reject a broken simulator far more reliably than they confirm a good one, so every measure prints
beside its verdict how many chances it had: compared days, days with an order, orders, fills.

```
SEGMENT 1 · sessions …
  Gate: comparable · release 1.5.0 · session 33989482 · backtest a1be0c79
  VERDICT: PASSED — no divergence beyond the bounds over the compared days — never "parity proven"
  Days: 30 compared, 0 excluded (longest run 0)
  measure            state        zone    k of N       share  run  reason
  decisions          passed       —       1 of 30      3.3 %    1  1 of 30 days one-sided, 1 explained
  execution_price    passed       yellow  2 of 41      4.9 %    1  …
```

A missing record is never a pass: a day whose decision cannot be read on one side, or an order
whose fee the venue did not report, is *not checked*. The floor of compared days holds each
measure that counts days to the days it judged: a day whose decision lies in a gap is left out of
measure 1, so measure 1 can fall below the floor where the segment does not.

**Yellow passes only where each order beyond the bound is traced to its cause**, and the trace is
printed under the order. Measure 2b traces a price to the market's move while the order waited for
its fill: held against each side's mid at its own fill, read from the archive's quotes, the
difference lies within the bound. Measure 2c traces a fee to the frozen maker rate: the order filled
resting, where the backtest charges the taker rate. Measure 2d traces a size to the venue's lot
step: the live size lies within one lot step of the backtest's, scaled by the two account values.
An order the measure cannot trace makes its zone red, whatever the count.

```
    fee_rate 2026-12-03 2026-12-03 14:02:11 fee rate 0.4000 % against 0.8000 % …
      traced: maker_rate — the venue charged 0.4000 %, the frozen maker rate 0.4 % — the order filled resting
```

A one-sided day whose workers agree is proposed as decided by the *account value* only where the
account value can have decided it: the decision logic's own outputs differ, the two account values
lie more than a cent apart, and an earlier order of the segment moved them apart — one only a side
sent, or one whose fill differs. Otherwise the day stays unexplained. `--reading rehearsal` reads a trial run the
same way, without the floor of compared days and with the per-order measures described rather than
judged, because a dry run simulates its fills on both sides. The words — *reading*, *zone*,
*divergence report*, *A/A* — are defined in the [Glossary](../glossary.md).

**Beside the verdict, a second backtest at the measured latency.** The backtest draws each order's
latency from the release's model, a few dozen milliseconds, while a live order takes as long as
the venue and the next tick take, often seconds. So wherever live orders filled, each segment is
backtested a second time with the latency drawn from the span the live orders measured, from
submission to fill, and measure 2b is shown against it:

```
  Beside the verdict — the backtest at the measured latency 640–3180 ms from 41 order(s), run …:
  execution_price    reported     green   0 of 41      0.0 %    0  41 order(s) within the bound
```

It never enters the verdict: it draws one latency for every order from the measured span, so it
shows how much of the divergence the latency model explains, not why one order differed — that is
2b's own trace, read from the order's record. Without a live fill it does not run, and says so.

The exit code says the same in one number. For `compare`: 0 every segment passed, 2 a segment was
not compared, 3 a compared segment failed or was not checked. For `aa`: 0 identical, 3 a window
differs, 2 refused or not checked.

## What each comparison keeps

Every run of either command keeps its own directory under `runs/divergence/<id>/`: the scenario
sets it generated, the warmup each segment's backtest started from, the run index it read a copy
through, and its report as JSON and as the text it printed. The backtests are runs of their own,
named in the report. The report reads on its own, without the copy it came from: see
[Data Storage Layout](../architecture/data_storage_layout.md), section *A divergence comparison
keeps its own record*.
