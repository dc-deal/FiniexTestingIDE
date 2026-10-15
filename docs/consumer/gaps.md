# Gaps in the archive

Bars stop for several different reasons, and from a duration alone you cannot tell which. Two hours
of nothing on a Sunday is a market that was shut; two hours of nothing on a Wednesday afternoon is
collection that failed — or, in a venue's published history, an afternoon nobody traded. Present
them alike and you will either raise an alarm every weekend or quietly miss real data loss. This
route names every interruption in a symbol's archive, gives each one a **category**, and says
**whose data** it lies in, so the difference is read off the answer instead of inferred from a
number.

**Routes**

```
GET /api/v1/brokers/{broker}/symbols/{symbol}/gaps
GET /api/v1/brokers/{broker}/symbols/{symbol}/gaps?origin_class=production&since=2026-01-01
```

| Parameter | Narrows `gaps` to |
|---|---|
| `origin_class` | the gaps in data of that origin class — see [whose silence it is](#whose-silence-it-is) |
| `since` | the gaps that start at or after this instant, ISO-8601; a value without an offset is read as UTC, and anything else — a bare year, a Unix time — is refused with `400 invalid_timestamp` |

Both are optional, and without them every gap is served. They narrow `gaps` only: `gap_counts` and
`gaps_by_year` always describe the whole archive, so the overview stays whole while the list is
cut to what you asked for. A symbol with years of a venue's history holds tens of thousands of
gaps, so a consumer interested in recent collection asks for it rather than reading everything.

**What is not here:** the data itself, and the span it lies in. Which venues and symbols exist,
what range they cover and the bars themselves are in [market data](/api/v1/docs/market-data). This
route describes the holes in that range and nothing else.

Reached with a grant naming the broker — `bars:<broker>`, the same grant the bar routes take.

## Per symbol, never per timeframe

There is no `timeframe` parameter, and that is not an omission. The coverage analysis runs at one
configured granularity, and a gap in the underlying stream is a gap at **every** timeframe above
it. An answer per timeframe would be the same answer, repeated.

## What one row is

```json
{"start": "2026-02-13T21:59:47+00:00", "end": "2026-02-16T22:03:11+00:00",
 "seconds": 259404.0, "category": "weekend", "reason": "…",
 "origin_class": "production", "origin_class_after": "production"}
```

| Field | Meaning |
|---|---|
| `start` / `end` | the interruption's own span, ISO-8601 UTC |
| `seconds` | how long it lasted |
| `category` | what kind of interruption it was |
| `reason` | the same judgement as a sentence, for a person to read |
| `origin_class` | whose data the gap lies in — the origin class of the tick file it lies in or follows; null where no tick file could be found for it |
| `origin_class_after` | the origin class of the tick file after the gap — different from `origin_class` only at a seam |

`keys.gaps` is `["start"]`: a gap is identified by **when it opened**. Two gaps of one symbol cannot
open at the same instant, and the category is a classification of the same interruption rather than
a second one. See [row keys](/api/v1/docs/row-keys).

`reason` is written for whoever reads the answer, not to be parsed — branch on `category`. It
begins with a status icon, names the classification with the measured duration, and ends with the
bar timeframe the gap was detected at, in brackets. It gives no advice: whether a gap calls for
checking the collector depends on whose data it lies in, which `origin_class` says.

## Whose silence it is

| `origin_class` | The data was recorded by | A gap in it is |
|---|---|---|
| `production` | a collector of ours in production | an interruption of our collection — worth looking into when it is long |
| `development` | a collector of ours in development | the same, on a development instance |
| `unknown` | a collector nobody has named | not attributable to an instance |
| `external` | nobody of ours — a venue's published history | a stretch in which nobody traded at the venue; there is nothing to re-collect |

`null` means no tick file could be found for the gap — at the very edge of the archive, typically.
Where `origin_class` and `origin_class_after` differ, the gap is the **seam** between two origins,
such as the end of a venue's history and our first collected tick.

A venue's history from the years when the market was thin holds a great many such silences: one
symbol's archive can carry a hundred thousand gaps in `external` data against a few hundred in our
own. `gaps_by_year` keeps the two apart.

## Per year and origin — `gaps_by_year`

```json
{"gaps_by_year": [
   {"year": 2025, "origin_class": "external", "short": 66, "moderate": 5, "large": 1,
    "hours_without_data": 21.9},
   {"year": 2026, "origin_class": "production", "short": 365, "moderate": 3, "large": 2,
    "hours_without_data": 46.7}],
 "keys": {"gaps": ["start"], "gaps_by_year": ["year", "origin_class"]}}
```

The gaps that mean missing data — `short`, `moderate`, `large` — counted per year and origin class,
with the hours they span; weekend and holiday closures are not counted, they are a market's expected
silence. A gap belongs to the year it **started** in. `keys.gaps_by_year` is `["year",
"origin_class"]`: a year holds one row per origin, and keying on the year alone folds them together.
Always over the whole archive, whatever the filters.

## The categories

| `category` | What it is |
|---|---|
| `seamless` | under five seconds — continuity, not an interruption |
| `weekend` | the venue's regular weekend closure |
| `holiday` | a closure of twenty hours or more containing a market holiday |
| `short` | a brief interruption, by default under thirty minutes |
| `moderate` | longer, by default under four hours |
| `large` | anything above that — worth looking into |

The boundaries between `short`, `moderate` and `large` are configured thresholds of this
installation's coverage analysis, so read the duration off `seconds` rather than assuming the
default.

`seamless`, `weekend` and `holiday` are expected; `short`, `moderate` and `large` are not. That
split is the one most consumers actually render.

A moderate gap on a market that closes is further distinguished in `reason` by whether it fell
inside or outside trading hours. The category is the same either way.

## Why a weekend is not a gap in the data

The categories come from the **market's own rules**, resolved from the broker's market type. A
venue that closes at weekends can produce `weekend` and `holiday`; one that never closes cannot,
and on it a quiet weekend is a real interruption that gets classified on its duration like any
other. So `weekend` appears on forex and never on crypto — not because crypto is treated
differently, but because crypto does not close.

A long gap that **spans** a weekend is split at the market's own boundaries before it is
classified, rather than being labelled a weekend in one piece. Without that, data loss on either
side of the closure would be absorbed into an expected one and never seen.

## `gap_counts` reports only what occurred

```json
{"gap_counts": {"weekend": 31, "short": 4, "large": 1}}
```

Categories that did not occur are **absent**, not zero — a zero is noise, and a table of empty
rows buries the one that is not. A category you never see for a symbol simply never happened
there; do not read its absence as a field that was not computed.

## Where the span comes from, and the one 404

The envelope repeats `broker` and `symbol`, and its own `start` and `end` are the span the analysis
ran over: the first and last bar it read at its granularity. That span is derived separately from
the range `/coverage` reports, so treat `/coverage` as the authority on what exists and this one as
the window these gaps were found in.

Interruptions are found from the **bar sequence**: a jump between two consecutive bars longer than
twice the expected interval is a gap. The doubling is jitter tolerance, so a jump of two bar
periods or less does not register at all.

That also fixes what the two ends mean. A bar's stamp is its open, so `start` is the open of the
last bar before the interruption and `end` the open of the first one after it — `seconds` is
therefore up to one bar period longer than the silence itself. Where a gap was split at a market
boundary, that boundary is the split point instead.

The gaps are served from a cache that is rebuilt when the underlying bars or its own
configuration change. Whose data each gap lies in — `origin_class`, `origin_class_after` and
`gaps_by_year` — is read from the archive's file index on every request, so it follows a new
import without waiting for the cache, and a request costs that read. A symbol the cache has not
analysed yet answers `404 coverage_report_unavailable` — that is "not computed", not "no gaps".
See [errors](/api/v1/docs/errors).
