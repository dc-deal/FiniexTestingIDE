# Why a field is null, and what to render instead

A null in this API has three different causes, and they look identical in the response. Render
them the same way and you will show "0" where the honest answer is "we never measured this", or
show a gap where the figure genuinely does not exist. The three:

| Cause | What it means | Can it change? |
|---|---|---|
| **Not applicable** | the figure has no meaning in this shape | no — it is correct here |
| **Not measured** | something that counts it was switched off | yes — a setting |
| **Not recorded** | the run predates the field | no — it is history |

The rule underneath all three: **a value that does not exist is null, never `0` and never the
empty string.** A zero read as a value and an empty string read as a name are the two mistakes
that cannot be undone further down, because by then they look like data.

**One route does not hold to it yet, and it is worth knowing before you write a renderer.** The
configuration directory (`/api/v1/directory` and its detail route) answers with the empty string
where a value is absent — a scenario set has no bot id, a window that is open has no end, a file
that never ran has no last run. Seven fields on a directory row behave this way. Treat `""` there
as absence, exactly as you treat `null` elsewhere; everywhere else in this API the rule above
holds.

## Not applicable: the figure has no meaning here

The commonest case is **one account's figure asked of several accounts**. A backtest of many
scenarios is many accounts; a live session is one. `final_equity` is the latest reading of one
account — over several there is no such reading, so it is null and the sum stands beside it as
`total_final_equity`. The same holds for `unrealized_pnl`, `open_position_count` and
`recovery_factor`.

Do not fill these in by adding. A sum of equities is a number, but it is not an equity: no
account ever held it.

The drawdown trio behaves differently again, and deliberately. Over several accounts it is the
**deepest** account's, never a sum, and `account_max_drawdown_unit` names which account that was.
Where no account declined, it names the first one.

The second case is a lifecycle position. An order that was not rejected has no
`rejection_reason`; an order that executed nothing has no `executed_price`. Nothing was lost —
the field simply has no subject yet.

## Not measured: a counter was switched off

In a backtest the decision tracker is **off by default**, because counting every decision costs
time on the hot path. With it off, the decision counters and the worker timings are null rather
than `0`: a zero would claim the logic decided nothing, which is a measurement nobody took.

`worker_decision_tracked` says which state a unit was in, so you never have to infer it. It is
the field to branch on. The same is true of the per-scenario signal counters — `buy_signals`,
`sell_signals`, `flat_signals`, `trades_requested` — which are null for the same reason.

Two worker figures are null for a narrower reason worth knowing, because both have a plausible
wrong reading: `compute_ratio_pct` is null when no tick was processed at all, and `ticks_idle`
is null when the worker never computed. The second one previously read `0`, which says "it
computed on every tick" — the exact opposite of what happened.

This cause is the only one of the three a caller can do something about: it is a setting on the
run, so a future run can carry the figure.

## Not recorded: the run is older than the field

The records on disk are not rewritten when a field is added. A run from before
`tick_timespan_seconds` existed answers null for it, and always will.

`parent_kind` is the sharpest example, because its null carries two meanings that are worth
separating. A run that stands alone has no parent, so there is nothing to name. A run indexed
before the field existed **may** have a parent whose kind is simply unknown. If you need the
distinction, `parent_id` tells you which you have: an id with no kind is the second case.

`run_purpose` is the one exception to "always will": for a run recorded before it, the value is
filled in when the run index is rebuilt, from the current declaration of its configuration, looked
up by the configuration's file name — and null before that rebuild and where the configuration can
no longer be read. `report_contract` has no such source and is null on every
run older than it. So are the four `origin_` fields of the run list on a run older than the
header's `origin` block.

## Null versus the empty list

They are not the same answer, and the run index is where the difference matters most.

`results` is **null** when the run-results ledger holds nothing for the run — it is still going,
it died before its close, or it was never commissioned to report. It is **`[]`** when the run
closed and had no figures to record. Null is "we do not know"; the empty list is "we know, and
there were none".

`artifacts` works the same way, and reads together with `reporting`: empty plus `expected` means
the run is still running or died before its report phase; empty plus `none` means it was never
meant to write any. Without the pair, a crashed run and a deliberately silent one look alike.

## Three states, not two

Some fields are deliberately three-valued and must not be collapsed:

- `reconciles` is `true`, `false`, or **null when the run reports no figure in that currency**.
  Null is an *absent check*, not a passed one. Rendering it as a tick is the one reading this
  field exists to prevent.
- `dry_run_declared` on a configuration row is `true`, `false`, or null — and null means the
  profile declares nothing, so the venue's own default applies and is resolved when the session
  starts. It is not "false".

## What to render

Show absence as absence. A dash, an em-dash, a greyed cell — anything that is visibly not a
number. Where the cause matters to your reader, the field beside it usually says which cause it
was: `worker_decision_tracked` for a counter, `reporting` plus `artifacts` for a run,
`parent_id` for a parent kind, `unit_count` for a figure that folded several accounts.
