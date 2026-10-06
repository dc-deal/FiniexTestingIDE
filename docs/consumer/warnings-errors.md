# Warnings and errors

A run that ended is not the same as a run that went well. Without this section "finished" covers a
clean backtest, one that logged forty errors and carried on, a session the operator stopped by hand
and one that shut itself down in an emergency — and nothing else tells them apart. This section is
the run's two notice channels, and the verdict the run was given.

**Routes**

```
GET /api/v1/reports/runs/{run_id}/warnings-errors
```

**What is not here:** the run's figures, and the outages it lived through. What the run earned is
[run summary](/api/v1/docs/run-summary); when a feed went silent, for how long and how often is
[feed stability](/api/v1/docs/feed-stability).

## Nothing here is decided when you ask for it

Every row was produced while the run ran — a validator decided it, or a logger recorded it. This
report only reads. That is why a warning can name the check that raised it, and why an observed
feed outage is deliberately *not* in here: an outage is a fact, and facts live in the
feed-stability section. What you get here is the judgement, if a check made one.

## Three parts

`warnings` are advisories, `errors` are what went wrong, and `outcome` is the run's grade plus the
counts behind it. Each list says what makes one of its rows unique:

```json
{ "keys": { "errors": ["name"], "warnings": [] } }
```

`errors` is one row per unit. `warnings` declares an **empty** key, and that is a statement rather
than an omission: a warning is an event, nothing folds two identical ones together, and a session
that logs the same message twice has two rows with the same text. See
[row keys](/api/v1/docs/row-keys).

An empty list means the run recorded none of that kind. It is not the same as the section being
missing, which is a 404 — see [errors](/api/v1/docs/errors).

## The grade: `outcome.run_outcome`

One classification, stamped once while the run ended, from the run's own result. Every surface
reads this field rather than re-deriving a verdict from the counts.

| Value | Meaning |
|---|---|
| `success` | every unit completed, nothing logged an error |
| `finished_with_errors` | the run completed, but errors were logged |
| `failed` | units failed, or the session ended in an emergency shutdown |
| `crashed` | the process did not complete — an uncaught exception, or no result at all |

Null where no grading was stamped — see [nulls](/api/v1/docs/nulls).

`failed_count` out of `total_units`, `failed_unit_names`, and `first_failure_name` /
`first_failure_error` describe which units failed and what the first one said. A live session is
one unit, so `total_units` is 1 there, and `failed_count` is 1 only where an emergency reason was
recorded.

## An emergency is not always a failure

`shutdown_mode` reads `normal` or `emergency` on a live session, and is empty on a backtest, where
it means *not applicable* and never *unknown* — [run kinds](/api/v1/docs/run-kinds) is how you tell
which of the two you are holding. An operator pressing Ctrl+C also arrives as `emergency`, so the
mode alone cannot separate a deliberate stop from a crash.

`operator_interrupted` is the discriminator. Only a deliberate interrupt sets it. Read the pair,
not the mode: `emergency` beside `operator_interrupted: true` is somebody stopping a session, and
`emergency_reason` carries the sentence where one was attached.

## A warning's tier says which channel produced it, not how bad it is

`tier` has two values, and their wire spelling reads like a severity although it is not one:

- **`major`** — a check decided it. `check` and `domain` are filled: `check` is the assertion's
  stable id, `domain` the area it belongs to.
- **`minor`** — the log pot, an observation nobody adjudicated. `check` and `domain` are **empty**,
  and that is the honest answer rather than missing data — no assertion decided a log line.

A `major` row is not necessarily more urgent than a `minor` one. It is more *attributable*:
somebody wrote the rule that fired, and `GET /api/v1/validation-checks` serves what every check id
means — its title and a sentence. `check` and `domain` are also empty on artifacts written before a
warning carried its origin.

`scope` is `run` for a run-wide notice, or the unit's name. The unit's name is the join into every
other section — see [row keys](/api/v1/docs/row-keys).

## Never count the warning rows

The two pipelines write the log pot differently. A backtest summarises its **whole** pot into one
row (`N warning(s) in M scenario log(s)`); a live session writes one row per entry. The same forty
warnings are therefore one row in one run and forty in the other.

The counts on `outcome` are the figure to read. They are counted the same way in both pipelines:

| Field | Counts |
|---|---|
| `error_count` | ERROR records in the error pot |
| `warning_count` | validator-produced findings — the `major` tier |
| `log_warning_count` | WARNING records in the log pot — the `minor` tier |

All three are null only where nothing recorded them, never zero. Artifacts written before the
counts existed were back-filled from their own rows, which is why a stored run answers the same
three numbers here as it does in the run list — see [runs](/api/v1/docs/runs).

## What an error row holds

One row per unit that carried any error, under the unit's `name` and `symbol`. Several channels
reach it, and they are not interchangeable:

| Field | What it is |
|---|---|
| `error_type` / `error_message` | the uncaught exception that ended the unit |
| `traceback` | that exception's trace, where one was captured |
| `validation_errors` | the checks that refused the unit before it ran |
| `logged_errors` | the error pot — records logged during the run without ending it |

One row can carry more than one of them, and the difference matters: a unit refused before it ran,
a unit that crashed, and a unit that logged errors and finished anyway are three different stories
with three different remedies.

A live session's row is narrower. `error_message` carries the emergency reason, and `error_type`,
`traceback` and `validation_errors` stay empty — a session has one unit and nothing to exclude, so
its startup checks stop the session rather than file a per-unit refusal.

## A logged error is a record, not a string

Every entry in `logged_errors` is an object:

```json
{ "level": "ERROR", "observed_at": "2026-09-27T09:14:02.511+00:00",
  "event_time": "2026-04-27T06:31:00+00:00", "scope": "eurusd_trend",
  "message": "order rejected by venue" }
```

`level` is always `ERROR` here — the list is the error pot, and the level travels with the record
instead of being implied.

**The two times are different questions.** `observed_at` is when the entry was recorded, on the
machine that ran it. `event_time` is the run's own clock — where in the *run* this happened. A
backtest replays history, so the two are far apart there and only the second one means anything for
placing an entry in the run. `event_time` is null where no clock was attached yet.

`scope` is the unit the entry belongs to, and **empty means run-wide**. Note the spelling differs
from a warning row, which writes run-wide as `run`.

## A 409 means the artifact is older than its shape

`logged_errors` once held bare strings. A stored artifact from before the record shape no longer
matches this model and is refused with `409 artifact_unreadable` rather than served or answered
with a `500`. Run output is regenerated rather than migrated, so the condition is named instead of
repaired. The full refusal vocabulary is in [errors](/api/v1/docs/errors).
