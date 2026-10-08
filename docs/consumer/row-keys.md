# What makes one row unique

An unordered list of objects says nothing about its own identity. Key on the obvious field and
two rows fold into one — silently, and in the direction that loses data. Both cases that prompted
this are ones where the obvious field is wrong: a deployment row is one per deployment **and**
account currency, and a booking period's running number restarts per bot, so two rows of one
deployment can both be number 1.

So every list says what makes one of its rows unique:

```json
{ "key": ["run_id"],                           "runs":        [ ... ] }
{ "key": ["sweep_id"],                         "sweeps":      [ ... ] }
{ "key": ["deployment_id", "currency"],        "deployments": [ ... ] }
{ "key": ["run_id", "currency"],               "sessions":    [ ... ] }
{ "key": ["run_id", "unit_name", "period_no"], "periods":     [ ... ] }
```

It is machine-readable on purpose. You can assert against it rather than read it, and a key that
changes shape is then something your tests see on the day it happens rather than something your
aggregates quietly absorb.

## A response serving several lists declares one key per list

A single key over two row types would name fields one of them does not have, so a response with
more than one list carries `keys` instead:

```json
{ "keys": { "units": ["name"], "aggregates": ["currency"] },
  "units": [ ... ], "aggregates": [ ... ] }
```

[Trade history](/api/v1/docs/trade-history) carries three lists and declares all three.
[Order events](/api/v1/docs/order-events) declares `events` and `broker_truth` alike —
`["scenario_name", "seq"]` — because `seq` runs across both lists within a unit.

## An empty key is a declaration too

It means the row's identity **is** its position in the list. A warning is an event; nothing folds
two identical ones into one, and a session that logs the same message twice has two rows with the
same text. In [warnings and errors](/api/v1/docs/warnings-errors), `errors` keys on `name` and
`warnings` declares `[]`.

A list with **no** key declared at all is a different statement: no field combination is unique
yet, and the work to give it one is still open. [Order history](/api/v1/docs/order-history) is in
that state.

## A trade is not a position

A partial close books several records of one position, and two scenarios of one symbol each count
from `pos_<symbol>_1`. Measured 2026-09-27 over the runs on disk, `position_id` alone repeats in
3 of 11 runs; the declared key repeats in none. The key for a trade row is the unit, the position
and the exit — all three.

## One unit, four field names, one identity

The same unit appears as `name` on [scenario details](/api/v1/docs/scenario-details),
[portfolio](/api/v1/docs/portfolio) and [venue account](/api/v1/docs/venue-account), as
`scenario_name` on [trade history](/api/v1/docs/trade-history) and
[order events](/api/v1/docs/order-events), and as `unit_name` on
[booking periods](/api/v1/docs/booking-periods).

In a backtest all four are the scenario's name. In a live session all four are the profile's
name, or its symbol where the profile declares none. One method produces it, so the four spellings
can never disagree about which unit they mean.

**The per-unit lists share that value, and that is a join, meant as one.** The roster in scenario
details knows every unit; the figures in `portfolio.units` only the ones that produced something;
`run-summary.units_absent` the ones that did not. Join them on the unit's name. A selection
carried by this value narrows every section at once, and a unit that traded nothing is present in
the roster with no trades rather than missing from it.

The one case where the name does not separate two things is a configuration naming one unit
twice. That is refused before the run starts, both copies are listed, and the refusal says why.

## This is not the same as a store key

Elsewhere in this project an entry's identity answers *how one entry is addressed*. A row key
answers *what makes one row of this response unique*, and the two differ wherever a route
aggregates: `/deployments` groups by deployment and currency, while the records underneath are
identified by run, currency, unit and period.
