# Which kind of run this is

Every record this API serves belongs to a run, and runs are not interchangeable. A backtest over
archived ticks, a rehearsal against a live feed that places nothing, and a session trading real
money all produce the same report sections — so a reader who treats them alike will present a
rehearsal's figures as money that moved.

Three fields say which kind you have, and none of them has to be inferred.

## The two axes

| | `orders_to: simulated` | `orders_to: venue` |
|---|---|---|
| **`ticks_from: archive`** | backtest · mock session | — |
| **`ticks_from: venue`** | dry run — a rehearsal on the live feed | real money |

`ticks_from` is where the ticks came from: `archive` is replayed from the tick archive,
`venue` is read from the venue as it happened.

`orders_to` is where the orders went: `simulated` means a simulator filled them, `venue` means
they were placed at the venue. **`venue` on `orders_to` is the only value that means money
moved**, and it is worth branching on explicitly rather than deriving it from anything else. A
deployment lists the values of its sessions in its own `orders_to` — both values mean it mixed a
rehearsal with real money; see [deployments](/api/v1/docs/deployments).

Both are null on a run recorded before these fields existed. See
[nulls](/api/v1/docs/nulls) for what to render then.

## `group` is the pipeline, never the nesting

`group` is `simulation` or `autotrader`. It says which of the two execution pipelines produced
the run — **not** whether the run stands alone.

This is the trap: a sweep combination is a `simulation` whose `parent_id` names its sweep, and a
live session that belongs to a deployment is an `autotrader` whose `parent_id` names the
deployment. Grouping by `group` therefore tells you nothing about structure.

## `parent_id` and `parent_kind`

`parent_id` names the thing this run belongs to — a sweep, or a deployment — and is null when the
run stands alone.

`parent_kind` says **which of those** the id names. Every parent id is a prefix plus a timestamp,
so from the id alone the two are indistinguishable. Do not derive the kind from `group`: it is
right today and will stop being right, because a run's parent may later be another run.

Its null carries two meanings. No parent at all, or a run indexed before the field existed, whose
parent kind is unknown rather than absent. `parent_id` tells the two apart: an id with no kind is
the second case.

## `reporting` says whether to expect anything at all

`reporting` is `expected` or `none`. Read it **together** with `artifacts`:

```
artifacts: []      reporting: expected   → still running, or it died before its report phase
artifacts: []      reporting: none       → it was never meant to write any
artifacts: [...]   reporting: expected   → the sections it actually produced
```

Without the pair, a crashed run and a deliberately silent one look identical. A run with no
artifacts at all exists as logs only.

## A unit is one account

In a backtest, each scenario is its own account with its own balance; a run of twenty scenarios
is twenty accounts. A live session is one. This is why a figure that describes one account —
`final_equity`, the drawdown trio — is null where several are folded together, and why a sum
carries a different name. [Nulls](/api/v1/docs/nulls) has the full rule.

A unit is named the same thing everywhere, under four different field names; see
[row keys](/api/v1/docs/row-keys).

## Data windows are declared, not measured

`data_windows` carries one window per unit, recorded when the run **started** — before a tick was
read. It is what the run was asked to cover. What a unit actually processed is on
[scenario details](/api/v1/docs/scenario-details).

`end_date` is null where the window is open: a run limited by a tick count rather than by a date,
or a session on a live feed, whose window is recorded at its start and never closed on the record.

There is deliberately no single window over all units. A span across several scenarios would
cover the gaps between them and claim the run touched a week none of its scenarios read.
