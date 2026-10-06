# Asking the server about itself

Some questions are not about a run or a venue, and every one of them is expensive to guess at. Did
this server restart since you last cached anything? Was your token accepted, or is nothing checking
tokens at all — both of those answer `200`. Which timeframe names will the bar routes take? And a
finding has just handed you a `check` id, which is a label nobody outside this API can read. These
routes answer those, and all but one of them need no credentials to ask.

**Routes**

```
GET /api/v1/health
GET /api/v1/caller
GET /api/v1/timeframes
GET /api/v1/validation-checks
```

**What is not here:** which contract this server serves and what moved into it. That is
`/api/v1/contract`, and [the contract log](/api/v1/docs/contract-log) explains how to use it. What
a refusal means is in [errors](/api/v1/docs/errors).

## Only `/caller` needs a token

`/health`, `/timeframes` and `/validation-checks` are open, and that is a decision rather than an
oversight. A timeframe list and a check vocabulary are this server's own static declaration — what
a name *means* — and say nothing about a venue, a run or an account. Gating the timeframe list
would make a market-data grant the precondition for a list that reveals nothing about market data.

`/caller` needs a token while gating is on, and takes no grant: a grant required in order to ask
what you hold would be circular.

## `/health` — whether it answers, and since when

```json
{"status": "ok", "version": "1.4.0", "started_at": "2026-10-06T07:11:02+00:00", "uptime_s": 1283.4}
```

`status` is `ok` whenever it answers at all. `version` is the application version.

`started_at` is when **this process** started serving, ISO-8601 UTC. It is new on every restart, so
a consumer that remembers it *sees* a restart instead of inferring one from a failed request.

`uptime_s` is the seconds since then, measured by the server on its own monotonic clock. You need
no clock of your own to tell a fresh server from a long-running one, and no agreement about whose
clock is right.

It is open, so it deliberately carries nothing a stranger could use — no commit, no host, no
authentication state. In particular it will not tell you whether your token works. That is
`/caller`.

## `/caller` — who the server takes you to be

A token says which **client** is calling. The account says on whose **behalf**.

```json
{"enforced": true, "client": "viewer", "account": "acc_ops", "account_kind": "person",
 "display_name": "Operations", "grants": ["bars:*", "reports:*"], "note": "browser client"}
```

| Field | Meaning |
|---|---|
| `enforced` | whether this server is checking tokens at all |
| `client` | the consumer your token authenticates as |
| `account` | the account that client acts for |
| `account_kind` | `person` or `service` |
| `display_name` | the account's human-readable name |
| `grants` | what your token may reach |
| `note` | the token's own note — who holds it |

Until a login exists, presenting a token **is** acting as its account, so this is also what a run
started through the API would record as the person behind it.

`account_kind: service` is a sibling service, not a person who happens to be named after one: a
record saying a run was started for a service must not read as though somebody by that name asked
for it.

### `enforced` is the server's state, never yours

This is the field the route exists for. Two situations look identical from a request, and only this
tells them apart:

- `enforced: false` — nothing verifies a presented token. Every identity field is null **even when
  you sent a valid one**, and `grants` is empty. A `200` here is not your token being accepted.
- `enforced: true` — a caller without a valid token never reaches this answer at all. It is refused
  with `401 unauthenticated`, which carries `WWW-Authenticate: Bearer`.

So a null `client` means *not checked*, never *not recognised*. Read `enforced` first and branch on
it; see [nulls](/api/v1/docs/nulls) for the general rule these nulls follow.

`500 identity_unbound` means a verified consumer is bound to no account. That is a defect on this
side and never an anonymous caller — the server refuses that state when it starts, so reaching it
should not be possible.

## What a grant is made of

A grant is `<surface>:<name>`. The **surface** is a group of routes; the **name** is the route's
first path parameter. So `bars:kraken_spot` is one venue's bar data and
`deployments:deploy_20260918_091413` is one deployment.

Where that first parameter is a generated id nobody would write into a token — a run id, for
instance — the realistic grant is `<surface>:*`. The model degrades to surface level there by
design rather than by accident.

A **list** route has no path parameter for a grant to be about. Those require at least one grant on
their surface instead — one `deployments:<id>` is enough to reach `/api/v1/deployments`, which
names no deployment in its path.

The vocabulary is closed. A grant naming a surface this server does not have is refused when the
credentials are read, at start-up — rather than becoming a request-time denial nobody can explain.
A route you hold no grant for answers `403 forbidden`.

Grants are read when the server starts, so a newly issued grant takes effect on a **restart** —
which is exactly what a changed `started_at` on `/health` tells you.

Each group of routes names the grant that reaches it in its own document;
[market data](/api/v1/docs/market-data) and [deployments](/api/v1/docs/deployments) are two.

## `/timeframes` — the names the bar routes take

```json
{"key": ["name"], "timeframes": [{"name": "M1", "minutes": 1}, {"name": "M5", "minutes": 5}]}
```

In ascending order by bar duration. `minutes` is there so you can sort, compare or label without a
table of your own — and so a name you have never seen before is still usable the moment it appears.

This list is the authority: a timeframe that is not in it is refused by the bar routes with
`400 invalid_timeframe`, and the refusal names the valid ones. It grows when this server's
configuration does, so read it rather than hard-coding a list.

One thing it does **not** govern: which timeframes exist for a given symbol. That is coverage, in
[market data](/api/v1/docs/market-data) — and coverage lists them alphabetically, so pair its names
with this list's `minutes` when you need them in duration order.

## `/validation-checks` — the vocabulary behind a `check` id

A finding names the assertion that produced it by a stable id. The id is what turns *which units
did warmup cost me* into a filter rather than a text search — and on its own it is a label nobody
outside this API can read.

```json
{"key": ["check"],
 "checks": [{"check": "scenario_boundary", "title": "No end to the scenario",
             "description": "The scenario sets neither an end date nor a tick limit, so nothing says where it stops."}]}
```

`check` is the id exactly as it is served elsewhere — in `run-summary.units_absent[].checks` and in
`warnings-errors.warnings[].check`. `title` is short enough for a label or a filter facet.
`description` is one sentence saying what the check asserts.

The list is complete in both directions: every id a finding can carry is declared in it, and every
entry in it is an id something emits. So an id you receive that is missing from **your** copy means
your copy is older than the server, not that the id was never declared.

## What to cache, and what tells you to drop it

`/timeframes` and `/validation-checks` change only when the server does. Fetch each once and keep
it; re-fetch when `/health` answers with a `version` or a `started_at` you have not seen before.
`/caller` changes when your token or its account changes, which is also a restart. `/health` is the
one meant to be asked repeatedly.
