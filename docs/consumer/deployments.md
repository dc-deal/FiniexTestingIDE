# Deployments

A live bot does not run once and stop. It restarts — for a deploy, after a crash, when the machine
reboots — and every restart is its own run, with its own id and its own report. Ask the run routes
about a bot that has been trading for a month and you get one session out of a dozen, with nothing
to say the other eleven exist or in which order they ran. A deployment is that sequence, held
together by one identity, and these two routes read it back as a single history.

**Routes**

```
GET /api/v1/deployments
GET /api/v1/deployments/{deployment_id}
```

**What is not here:** the money, day by day. A deployment's trading days are their own route and
their own shape — see [booking periods](/api/v1/docs/booking-periods), which covers both the
run-scoped and the deployment-wide one. A single session's report sections are reached by its
`run_id`; see [runs](/api/v1/docs/runs).

Reached with a grant naming the deployment — `deployments:<deployment_id>`, or `deployments:*`. The
list route has no path parameter for a grant to be about, so it instead requires at least one grant
on that surface; [the server's own routes](/api/v1/docs/server) has the grammar.

## One row is one deployment and one account currency

```json
{ "key": ["deployment_id", "currency"], "deployments": [ … ] }
```

A P&L column added up over two currencies is not a number, so a bot that traded in two of them is
**two rows**, each complete within its own. Key on `deployment_id` alone and the two fold into one,
silently, in the direction that loses data. See [row keys](/api/v1/docs/row-keys).

`sessions` on such a row counts the sessions **in that currency**, not the deployment's sessions in
total.

The list is newest first. The detail route is oldest first — a life reads forwards.

## What adds up, and what never does

`net_pnl` is summed over the row's sessions. This one genuinely adds up.

`max_drawdown` is the **largest** of them and never a sum. Each live session carries the running
decline measured against the peak it inherited, so adding them counts one decline once per session
that was still inside it. The row reports the deepest session's figure, and `max_drawdown_pct` is
that same session's share of the peak standing at the time — not a percentage of anything else on
this row.

`longest_gap_hours` is the longest stretch the bot was not running, over the gaps that could be
measured; null when none could.

`first_started` and `last_started` are the first and the most recent session's start.

## `changed` is a flag; the advisory is the reason

`changed: true` means the row's sessions were **not** all produced by one configuration. It is a
one-bit summary for a list a reader is scanning to decide what to open. What actually moved is on
the detail route, as the advisory.

## Real money or a rehearsal — `orders_to`

`orders_to` on a row lists where its sessions' orders went, each value once: `["venue"]` is a
deployment that traded real money, `["simulated"]` one that rehearsed — a dry run or a mock
session — and both together a deployment that did each in different sessions. A mixed row is also
`changed`, because its `net_pnl` adds simulated fills to real money. Empty when none of its sessions
recorded it — a session recorded before contract 23 does not say. A single run says the same in the
run list, as `orders_to`; see [run kinds](/api/v1/docs/run-kinds).

## What one session says

| Field | Meaning |
|---|---|
| `index` | its position in the deployment, 1-based |
| `run_id` | the session's own run identity, and the way into its report routes |
| `started` | when it began |
| `ended` | when it stopped |
| `ran_hours` | how long it ran |
| `gap_hours` | hours the bot was **not** running before it |
| `gap_between_starts` | whether that gap could only be measured the coarse way |
| `net_pnl` | realised P&L of that session |
| `max_drawdown` / `max_drawdown_pct` | the cumulative account decline as of that session |
| `orders_to` | where its orders went — `venue` is real money, `simulated` a rehearsal; null when not recorded |
| `strategy_changed` / `operation_changed` / `orders_to_changed` | what differed from the session before |
| `bot` / `bot_id` | which bot ran it |
| `currency` | the row's account currency |

`key` is `["run_id", "currency"]` — the same two-part identity as the list, one level down.

`max_drawdown` on a session row is **cumulative**, measured against the inherited peak, never that
session's own decline. It is why the list's figure is the largest of these rather than their sum,
and it means `max_drawdown` and `max_drawdown_pct` are the two figures on the row that are not
about that session alone.

## Each currency is its own series

`index`, `gap_hours` and the two change marks restart per currency. The first session of the next
currency has no predecessor, so nothing is measured against the last session of the other one.

A fixture captured before this changed holds those figures measured across currencies instead;
[the contract log](/api/v1/docs/contract-log) names the version it moved in.

## The time fields, and the one that overstates

`ended` is when the session's ledger row was written, which is when it stopped. It is null on a row
written before that stamp existed.

`ran_hours` is derived from the two stamps rather than stored, so it is the session's length **plus
the seconds its reports took**. Null when either end is unknown — an unmeasurable duration reports
as unmeasured rather than as a number nobody can check.

`gap_hours` runs from the predecessor's close to this session's start. It is null for the first
session of a currency series, which has no predecessor.

`gap_between_starts: true` is the warning on that figure. It means the gap could only be measured
from one **start** to the next, because the predecessor's row predates the close stamp — so it
contains the predecessor's whole runtime and **overstates** the downtime. A bot that ran 06:00 to
18:00 and came back at 19:00 is a one-hour gap measured properly, and a thirteen-hour one measured
this way. It is marked rather than quietly shown as the same measure.

`started` is null where the stored timestamp could not be read, and `ran_hours` and `gap_hours`
follow it. See [nulls](/api/v1/docs/nulls).

## The change marks, and what each one means

`strategy_changed` is what the bot **decided** — its parameters differ from the previous session's.

`operation_changed` is what a session **did** without changing what it decided: a safety threshold,
a guard, a timeout, the capital declaration.

`orders_to_changed` is where the orders **went**: a rehearsal followed by real money, or back. It is
false where either of the two sessions did not record it — not recorded is not a change.

All are false on the first session of a currency series, which has nothing to differ from.

## The advisory says whether the history is one series at all

The per-session marks say *where* something changed. The advisory says *whether* the rows may be
read as one series, and it says it **before** the table rather than inside it. On a thirty-day run,
eleven sessions with three parameter changes show three marks somewhere in the middle, and the
reader has already added up the P&L column by the time they reach them.

| Field | Meaning |
|---|---|
| `sessions` | how many sessions it is about — distinct runs, not rows |
| `strategy_stands` | how many distinct parameter sets the deployment ran under |
| `operation_stands` | how many distinct operational configurations |
| `orders_to` | where the sessions' orders went, each value once — both values mean real money and a rehearsal in one history |

**Null is an answer, not a gap.** It means there is nothing to report: one strategy stand, one
operational stand and one place the orders went, across the whole deployment. A deployment of a single session answers null too,
having nothing to compare against. A warning that fires on the normal case is a warning that gets
skipped.

It renders no verdict. Whether the halves of a changed history may be compared is a judgement a
person makes with what it reports.

## `unfinished`: the sessions that are not in the list

`unfinished` counts this deployment's runs that never reached their close. They are absent from
`sessions` by construction: the row behind a session is written as the session closes, so a run
killed before that leaves a start record and no session at all. Without the count, a short list
would be a number you had no reason to doubt.

A session running **right now** looks exactly the same — a start record and no close. Render it as
*not completed*, never as *aborted*; whoever reads it knows whether a bot is up.

The count can be low rather than wrong: a run whose record does not say what kind of thing it
belongs to is not counted here, because an unknown kind is not a claim that it is a deployment.
[Run kinds](/api/v1/docs/run-kinds) explains that field and its null.

## `bot` and `bot_id` answer different questions

`bot` is what the profile is **called**, and an operator improves a name. `bot_id` is the
**declared** identity and the only one that does not move; it is empty on a profile that declares
none.

Two deployments sharing one `bot_id` are one bot whose deployment identity was deliberately
restarted. A fresh deployment identity records no link back, so without `bot_id` a deliberate
restart reads as two unrelated bots — and a reader asking *is this the same bot as the row above*
has no other column to ask.

## No reconciliation line, and there cannot be one

A run's booking periods can be checked against the figures the run itself reports. Across many runs
there is no single run summary to sum against, so the second, independent derivation that makes a
check a check does not exist here.

It is stated rather than left out: a missing check read as a passed one is the more expensive
mistake. [Booking periods](/api/v1/docs/booking-periods) describes the check this refers to, and
its deployment-wide route carries the same absence for the same reason.

## Where these rows come from

The run-results ledger, and nothing else. A deployment has no run directory, no header and no
artifacts of its own, so the report routes have nothing to say about one — they are addressed by
`run_id`, which is what every session row carries for exactly that reason.

An identity no ledger row names answers `404 deployment_not_found`. An **empty** `/deployments` is
a state and not a failure: no profile has declared a continuous deployment yet.
