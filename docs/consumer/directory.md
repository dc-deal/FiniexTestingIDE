# The configuration directory

The run index tells you what ran. It cannot tell you what *can* run: a configuration nobody has
used yet has no run to appear under, and the file somebody is editing at this moment does not even
parse. This section is the other list — every scenario set and every AutoTrader profile a run can
be started from, what each one declares, and what the newest run started from it did.

**Routes**

```
GET /api/v1/directory
GET /api/v1/directory/{file}
```

**What is not here:** the configuration a given run was actually commissioned with. A row says
what a file declares *today*; what a run was handed is frozen under that run's own id — see
[config](/api/v1/docs/config) and [runs](/api/v1/docs/runs).

## A row is a file, read and not validated

```json
{ "key": ["file"], "rows": [ ... ], "count": 31 }
```

Newest-changed first. `key` is the **file name alone**: one name is one entry across every root
and both kinds, resolved by precedence before the directory knows what the file holds. It was
`["kind", "file"]` before contract 9. See [row keys](/api/v1/docs/row-keys).

`status` is `readable` or `unreadable`, with `reason` saying why. It is never a validation verdict
— validation happens when a run starts; this list only reads. **A file being edited is
`unreadable` for minutes at a time, and that is a state to show rather than an error to raise.**

One kind of `unreadable` is not about the file's own bytes at all: its **name** is taken by a
configuration of the other kind. A run records its configuration by file name, so such a file
could not start a run anyone could trace afterwards — it is refused, and that is what the row's
reason says. Since contract 11.

`kind` is `scenario_set` or `autotrader_profile`, and null where the file could not be read.

## Where a file lives, and what it shadows

`origin` is `user_configs`, `user_algos` or `configs` — and that is also the order in which a
same-named file wins. `shadowed` names the origins of the same-named files this one wins over,
which is the answer to "why does my edit over there have no effect".

`folder` is the sub-folder a file sits in within the `configs` origin, and empty for the other
two, whose layout is deliberately not served. **A path never leaves this server**: a row names its
file and its origin, never where on disk it lies.

## What the file declares

Read from the file's own JSON, after the per-scenario cascade that gives each scenario its
strategy settings:

| Field | Meaning |
|---|---|
| `name` | the scenario set's or the profile's own declared name |
| `modified_at` | when the file last changed |
| `scenarios_declared` · `scenarios_enabled` | every scenario the file names, and the ones that would actually run |
| `symbols` | the distinct symbols of the enabled scenarios |
| `data_broker_types` · `market_types` | the brokers whose archives those scenarios read, and what those brokers are |
| `decision_logics` · `workers` | the distinct strategy components they resolve to |

A profile is one unit: both of its scenario counts read 1.

`data_broker_types` is the broker whose **archive** is read, which for a profile is the broker its
scenario settings name, or its own broker where they name none. It has been called this since
contract 16, so that one word means one thing across this API.

`market_types` is `crypto`, `forex`, or `unknown` for a broker this server has no market type
configured for.

Three fields describe a profile only and are empty or null for a scenario set:

- `bot_id` — the bot's declared identity
- `adapter_type` — `mock` or `live`
- `dry_run_declared` — three-state. `true` and `false` are what the profile declares; **null means
  it declares nothing**, so the venue's own default applies and is resolved when the session
  starts. It is not `false`. See [nulls](/api/v1/docs/nulls), and
  [run kinds](/api/v1/docs/run-kinds) for what the resolved answer means.

## What has been run from it

| Field | Meaning |
|---|---|
| `run_count` | runs on record that were started from this file |
| `last_run_at` · `last_run_id` | the newest of them |
| `last_run_figures` | what that newest run did |

The figures are matched out of the run index on the file name a run recorded and on the run's
type, so a run whose records have been pruned no longer counts here.

`last_run_figures` is the same shape the run index serves on each of its rows — `results` per
account currency, `run_outcome` and the warning and error counts, the ledger join contract 15
added there. It is null when there is no run at all, and also when the ledger holds nothing for
the newest one.
[Runs](/api/v1/docs/runs) explains those figures and the three states of `results`.

`run_count: 0` is an answer and not a gap. A configuration that has never been used is precisely
what this list exists to show.

## How fresh a row is

The list is served from a cache refreshed at most every 30 seconds. `?refresh=true` walks the
configuration roots now instead. Reading changes nothing but that cache.

## One file in detail

`GET /api/v1/directory/{file}` takes the file name exactly as the list spells it, and answers with
three things: the file's row, its scenarios read **fresh** from the file, and the ids of the runs
started from it, newest first.

`scenarios` is keyed by `name` and is empty both for a profile and for an unreadable file. One
scenario says:

| Field | Meaning |
|---|---|
| `name` · `symbol` | the scenario, and what it trades |
| `data_broker_type` · `market_type` | whose archive it reads, and what that venue is |
| `start` · `end` | its window as written; `end` is empty where the window is open |
| `max_ticks` | its tick cap, null where it runs by time instead |
| `enabled` | false where the set switches it off |
| `decision_logic` | its strategy after the cascade |

A name no file goes by is `404 config_file_not_found` — see [errors](/api/v1/docs/errors).

## How absence reads here

On these rows an absent **string** is empty rather than null: `name`, `reason`, `folder`,
`bot_id`, `adapter_type`, `last_run_at` and `last_run_id` all read `""` when there is nothing to
say, and a scenario's `end` reads `""` where its window is open. Treat those the way
[nulls](/api/v1/docs/nulls) says to treat a null — as absence, not as a value.

The two fields that are genuinely three-state, and must not be collapsed into two, are
`dry_run_declared` and `last_run_figures`.
