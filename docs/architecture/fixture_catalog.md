# Fixture Catalog

A consumer tests against runs it pins in this archive — a run carrying every report state, a
deployment with a history. Nothing on this side used to know they were pinned: a deployment history
produced for the viewer on 2026-09-27 vanished when the archive was rebuilt on 2026-10-07, and the
consumer found out from a failing capture. The catalog turns those runs into a committed procedure:
each entry is produced by one command, checked against the properties its consumers assert, and
protected while it is the one they pin.

**Not here:** what a run's `run_purpose` means to a reader of the API — that is
[Run Kinds](../consumer/run-kinds.md). How a test's run is held to `fixture` — the
[test runner documentation](../tests/tests_runner_docs.md), section *Every run a test starts is a
fixture*.

## An entry is declared data

`FIXTURE_CATALOG` in `python/framework/fixture_catalog/fixture_catalog.py` holds every entry, and
`python python/cli/fixture_catalog_cli.py list` prints them with their current production. An entry
names:

- **what produces it** — a scenario set (one backtest), a profile (one AutoTrader session), a
  session sequence (several sessions of one bot, each with its own changes, which makes a
  deployment with a history), or a sweep
- **the configuration it starts from** — committed, and declaring `"run_purpose": "fixture"`
- **its properties** — each one sentence a consumer can read, and a check over what the API serves
- **its consumers** — who relies on it

A session sequence declares its sessions as data: the replay day each one starts at, the values it
changes on the base profile, whether it starts a new deployment history, and whether it is killed
before its close:

```
s1 · a fresh history            2026-02-01  rsi_oversold 40  max_drawdown_pct 25   --new-deployment
s2 · the same configuration     2026-02-02  rsi_oversold 40  max_drawdown_pct 25
s3 · the STRATEGY changed       2026-02-03  rsi_oversold 35  max_drawdown_pct 25
s4 · the OPERATION changed      2026-02-04  rsi_oversold 35  max_drawdown_pct 12
s5 · killed before its close    2026-02-05  rsi_oversold 35  max_drawdown_pct 12   killed at its first order
s6 · a second history           2026-02-06  rsi_oversold 35  max_drawdown_pct 12   --new-deployment
```

## Producing an entry

```
python python/cli/fixture_catalog_cli.py produce report_coverage
python python/cli/fixture_catalog_cli.py produce --all
python python/cli/fixture_catalog_cli.py verify demo_deployment
```

`produce` runs the entry in the ordinary stores — the runs are the product, so they land where a
consumer reads them. A backtest and a sweep run in the producing process, through the same entry
points the strategy runner and the optimization command use. An AutoTrader session runs through its
own command line in a process of its own, because a session that must never reach its close can only
be produced by killing one.

A session is killed at a moment it reaches, never after a fixed time: the production watches the
session's own order event stream and kills it once its first order is there. The session is then
past its boot and trading, so the run it leaves behind dies with an order on it, the way a real
session dies. A timer could not tell where the session was — the first order arrives about 25
seconds after the start on an idle machine, and later on a busy one. A session that does not reach
its moment within three minutes is killed anyway; one that ends on its own first is left alone; and
either way the production says so.

The production then finds its runs — those that appeared while it ran and carry the entry's run
name, or belong to its sweep — and checks every property against them through the readers the API
serves from: a run's sections from the report store, a deployment and a sweep from the route
functions that answer for them. A property therefore holds for what a consumer is SERVED.

`verify` checks the current production again without producing anything — after a contract change,
it says whether the pinned runs still carry what their consumers assert.

## The production record

`runs/fixture_productions.jsonl` records every production, one line each: the entry, when it
finished, the runs, deployments and sweeps it made, whether every property held, how each of its
AutoTrader sessions ended, and the contract it ran under. A killed process cannot leave a word in
its own run, so why a session was killed — or why it could not be — travels in the record, and a
production that fails its check prints it beside the properties that did not hold. Which code made
the runs is on each run's own header, so the record does not repeat it. It is a RECORD store of
one append-only file at the root of the run tree — read whole, because it holds a handful of lines
per entry, and placed beside the run index because it describes runs in that tree and has to die
with it. Its location is derived from the run index's, never configured a second time.

**The CURRENT fixture of an entry is derived from it, never stored**: it is the newest production
whose runs carried every property. A production that failed its check stays in the record as what
happened, and the previous one stays current. Nothing is written into a run.

## A production replaces another, it does not delete it

A new production makes new runs beside the old ones. The new ids go to the consumer in one
message; the old runs stay until the consumer has moved to them. Only the operator knows when that
is, so the pruner keeps the runs of a superseded production that passed its check until it is told
`--release-fixtures`. The current production is kept whatever it is told: releasing it together
with the old ones would let a newer run of its family push it past `--keep-last` and delete the ids
the consumer has just moved to. An entry renamed or removed from the catalog has no current
production any more — nothing can produce it again — so its runs count as superseded. A production that failed its check protects nothing: nobody was ever
told to pin it.

## What protects a pinned run

| What happens to a run | What decides it |
|---|---|
| produced again | a new production beside the old one; the run list serves `fixture_superseded: true` on the old runs, derived from the record each time — nothing in the old run changes |
| pruned | the pruner keeps every run of the current production by every selector, and the runs of a superseded production that passed its check until `--release-fixtures` is given ([CLI Tools](../cli_tools_guide.md)) |
| the archive rebuilt | `produce --all` is the fixture step of a rebuild: the entries come back as new productions, and their ids go to the consumer in one message |
| a certificate run | never produced here — the catalog holds fixture entries only — and never pruned: the pruner keeps a run whose own header says `certificate`, read from the header on disk rather than the index |

The real-money field-study runs that predate `run_purpose` carry no purpose in their headers; they
stay protected by the older guard that keeps any run holding `field_study.jsonl`.
