# What runs on this machine

Several things run on one machine at once — backtests, a parameter sweep, an AutoTrader session,
an import, the test suite, this server — started from different places, sometimes from different
copies of the code. When memory runs out, the operating system ends one of them, and afterwards
nobody can say which runs held what. This route says which activities run right now, who started
them, from which code, and how much memory each one's processes hold.

**Route**

```
GET /api/v1/activities
```

**What is not here:** how busy the machine's processors are — that is `/machine-resources` in
[the server's own routes](/api/v1/docs/server). What a run DID is [runs](/api/v1/docs/runs), and
whether a session trades real money is the run's kind, which the run list states for its `run_id`
([run kinds](/api/v1/docs/run-kinds)).

Reached with any grant on the `machine` surface — `machine:*` is the one to issue. The route has no
path parameter for a grant to name, and a row names scenario sets, profiles, run ids and command
lines, so nobody without a grant is shown it. `/machine-resources` takes the same grant.

## An answer

```json
{"key": ["activity_id"],
 "measured_at": "2026-10-18T14:41:07+00:00",
 "machine_memory": {"limit_bytes": 32495570944, "used_bytes": 18790481920, "used_pct": 57.8,
                    "limit_source": "machine", "warn_at_pct": 80.0, "critical_at_pct": 90.0},
 "pressure_level": "ok",
 "pressure_reasons": [],
 "server_activity_id": "act_20261018_091200_5be1c0d2",
 "unreadable_activity_ids": [],
 "activities": [
   {"activity_id": "act_20261018_143102_9d04c2aa", "activity_kind": "backtest",
    "activity_state": "running", "activity_label": "my_set__sweep_20261018_140200_ab12cd34_c003",
    "run_id": "20261018_143102_e1f2a3b4", "sweep_id": null,
    "parent_activity_id": "act_20261018_140211_3f9c1a7e",
    "pid": 48211, "command_line": "python/cli/optimization_cli.py run my_sweep.json",
    "started_at": "2026-10-18T14:31:02+00:00", "uptime_s": 605.2,
    "working_tree": "release-v1.5.1", "git_commit": "301db02f",
    "origin_channel": "sweep", "origin_client": "console", "origin_principal": "operator",
    "origin_host": "h_3k9x2a", "console_session": null,
    "rss_bytes": 0, "process_count": 0},
   {"activity_id": "act_20261018_140211_3f9c1a7e", "activity_kind": "sweep",
    "activity_state": "running", "activity_label": "my_sweep",
    "run_id": null, "sweep_id": "sweep_20261018_140200_ab12cd34",
    "parent_activity_id": null,
    "pid": 48211, "command_line": "python/cli/optimization_cli.py run my_sweep.json",
    "started_at": "2026-10-18T14:02:11+00:00", "uptime_s": 2336.4,
    "working_tree": "release-v1.5.1", "git_commit": "301db02f",
    "origin_channel": "cli", "origin_client": "console", "origin_principal": "operator",
    "origin_host": "h_3k9x2a", "console_session": null,
    "rss_bytes": 7623566950, "process_count": 7}]}
```

## What a row says

| Field | What it says | Null when |
|---|---|---|
| `activity_id` | the activity's own id, and the row's key | never |
| `activity_kind` | `backtest`, `sweep`, `autotrader_session`, `import`, `discovery_cache_build`, `test_suite` or `api_server` — a kind you do not know is shown by its value | never |
| `activity_state` | `running` on every row today; `queued`, `finished` and `failed` are declared for a queue that will write into the same list | never |
| `activity_label` | what it is about, for a person: a scenario set, a profile, a sweep, an import's id | never |
| `run_id` | the run it writes, to look up in [runs](/api/v1/docs/runs) | it writes no run |
| `sweep_id` | the sweep it is, to look up in [sweeps](/api/v1/docs/sweeps) | it is no sweep |
| `parent_activity_id` | the activity it runs inside — a sweep's backtest names its sweep | it runs inside none |
| `pid` | its process id — information only, meaningful on this machine while it runs | never |
| `command_line` | the command that started it | never |
| `started_at` | when it started, ISO-8601 UTC | never |
| `uptime_s` | seconds since then | never |
| `working_tree` | the code it runs from: `.` for the code this server runs from, otherwise that code tree's name | never |
| `git_commit` | the commit its code runs from | none could be read |
| `origin_channel` · `origin_client` · `origin_principal` · `origin_host` | who started it, the way the run list states it: through what, which client, for whom, on which installation | not recorded |
| `console_session` | the console session it was started from, where this installation names one; a session named by a path leaves by its last part | none is named |
| `rss_bytes` | the resident memory of its processes | a process could not be read, or the activity's own process is gone — never `0` in its place |
| `process_count` | how many processes `rss_bytes` covers | never |

## A row is the activity, never its process

The key is `activity_id`. A process id is reused by the operating system, and two activities can
run in one process: a sweep and the backtest of its current combination share one, as in the
answer above. A row whose `parent_activity_id` names an activity that is no longer listed ran
inside one that has ended.

## Memory counts each process once

Every process of the machine counts toward the nearest activity above it: a backtest's workers
toward the backtest, the test suite's processes toward the suite. Where several activities share
one process, it counts toward the outermost — the one that started it — and the inner one shows
`process_count: 0` and `rss_bytes: 0`: its memory is its parent's. So the rows add up without
counting a process twice.

`rss_bytes` is resident memory, and an upper bound: a process started as a copy of another still
shares pages with it, and both count them. Where any process of an activity could not be read,
`rss_bytes` is `null` rather than a sum that leaves it out.

## Only what runs is listed

An activity enters the list when it starts and leaves when it ends. One that was killed without
leaving drops out at the next reading — unless a process it forked lives on: a backtest's scenario
workers keep its entry when the backtest is killed, and its row stays, with `rss_bytes` null,
until they end. So the list is never a history: what ran is [runs](/api/v1/docs/runs). A missing
list is an empty one — nothing has started yet. A list that exists and cannot be read answers
`500 activity_registry_unreadable`, because "could not read" is not "nothing runs"
([errors](/api/v1/docs/errors)).

`unreadable_activity_ids` names entries this server cannot read or judge — written by a different
version of the code on the same machine, or kept where this server may not look. They are left as
they are, and they may hold memory all the same.

## One instant

`measured_at` is taken first; the list, every row's memory and `machine_memory` are read right
after it. `machine_memory` and the pressure verdict are the same block `/machine-resources`
serves, so the rows' memory and the machine's limit can be drawn on one scale.

`server_activity_id` is the row of the server answering. It is `null` when the server is not in the
list — started in a way that does not enter it — and then the list cannot be trusted to be
complete.

## No path of this machine is served

A command line names its paths relative to the code tree the activity runs from — the value of an
option written as `--name=value` too — a code tree is `.` or a name, never a path, and a console
session named by a path leaves by its last part. The layout of the machine stays on the machine.

## How often to ask

Every few seconds is what this route is made for. One reading costs about a tenth of a second on
the server — measured on 2026-10-19 with some 1,300 processes — almost all of it reading the
machine's process table; it touches no file of the
project's data. Hold the last answer and show its `measured_at`, so a reading that has stopped
arriving is visibly old rather than silently current.
