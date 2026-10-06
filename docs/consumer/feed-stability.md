# Feed stability

A ratio cannot tell one long outage from forty short hiccups. Both produce the same share of stale
ticks, and the two demand completely different reactions — one is a source that fell over for an
hour, the other is a line that flickers. This section answers *when* and *how often*: every stretch
in which a data source was observed to go stale, with its measured span, for the market data and
for every signal source alike.

**Routes**

```
GET /api/v1/reports/runs/{run_id}/feed-stability
```

**What is not here:** what the strategy decided on, and what the source was. The per-scenario
fresh / stale / blind counts and a signal source's provenance are [signal](/api/v1/docs/signal).
The judgement that a run is not to be read as clean — a run carrying injected faults, for one — is
a warning, and warnings live in [warnings and errors](/api/v1/docs/warnings-errors).

## Observed, never planned

Every timestamp in here comes from the moment a source was *seen* to change state. A configured
fault window contributes its label and nothing else, and the two legitimately disagree:

- a planned 60-minute outage shows up as the staleness it actually caused, which is shorter,
  because a staleness threshold has to elapse before anything is flagged;
- a window reaching past the end of the run is recorded only as far as the run got.

That is the point of the split. Intent is one record, experience is another, and averaging them
would hide exactly the case worth seeing.

## A run with no rows is a run with no disturbance

`units` is empty when nothing went stale at all. That is a measured "none", not an absent
measurement — the rows are built from observed state changes, and a run that never saw one has none
to report.

The consequence is that a source only appears here once it has been disturbed. A source that ran
cleanly throughout contributes no row, and so its tick counters are not in this section either.

## What one row is

One data source in one domain. This response declares no row key, so read `source` plus `domain` as
a description of a row rather than as a contract — see [row keys](/api/v1/docs/row-keys).

`domain` has two values, and they are different kinds of input:

- **`tick`** — the market-data stream itself. `source` is the broker.
- **`signal`** — one external signal source. `source` is the source the unit bound, or `(signal)`
  where the unit bound no named one.

## The episode

| Field | Meaning |
|---|---|
| `stale_from` / `stale_to` | when the source was seen to go stale, and when it recovered |
| `duration_seconds` | the measured length of the outage |
| `unit` / `symbol` | the run unit that observed it, and that unit's symbol |
| `origin` | `live-real` or `stress-injected` |
| `label` | the fault event's label — empty for a real outage |

**An empty `stale_to` means the source never recovered.** The run ended inside the outage. Render
it as open-ended; do not read it as a zero-length episode or as a missing value.

**Use `duration_seconds`, do not subtract the two stamps.** The duration is measured — on the wall
clock in a live session, on the run's own clock in a replay, and
[run kinds](/api/v1/docs/run-kinds) says which you are holding. A replay session runs both clocks
at once, so the subtraction can come out different there. The measured figure is the authoritative
one.

`origins` on the row lists the distinct origins across its episodes, so a source that saw both a
real outage and an injected one says so in one field.

## One outage, many episodes

Each episode is recorded by the unit that **observed** it. A backtest of twenty scenarios over the
same window on the same signal source records that source's archive hole twenty times — once per
unit, each a legitimate observation of the same silence.

So `episode_count` is not the number of distinct outages, and it is not comparable between a run of
one unit and a run of twenty. `unit` is what separates the rows; group by it before you count.

## The counters beside the episodes

`fresh_ticks`, `stale_ticks` and `blind_ticks` describe the **whole run** for that source, summed
across its units, while the episodes describe only the disturbed stretches. Read together they
answer the two questions a stability view has to answer at once: how much of the run was decided on
degraded data, and when.

**`blind_ticks` is a signal-domain figure only.** A tick stream either delivers or is silent, and
silence is measured as the last known tick ageing — there is nothing a market-data stream can fail
to resolve. On a `tick` row the field stays zero and means nothing. What `blind` means on a signal
source is in [signal](/api/v1/docs/signal).

## The totals, and the one that is not a span

| Field | What it adds up |
|---|---|
| `episode_count` | every episode in the response |
| `stale_seconds` | every episode's measured duration |
| `stress_injected_count` | how many of those episodes were injected rather than real |
| `source_count` | how many rows the response holds |

`stale_seconds` is a **sum of durations, not a stretch of the run**. Two sources stale at the same
moment each contribute their seconds, and so does each unit that observed the same outage. It can
therefore exceed the run's own length, and comparing it against the run's duration produces a
meaningless percentage. It is a magnitude to compare across runs of the same shape, not a share.

`stress_injected_count` against `episode_count` is the ratio worth rendering: it says how much of
what you are looking at was deliberate.
