# Indicator series — what a worker draws on a run's chart

A strategy decides on what its workers compute — an RSI, a set of Bollinger bands — and a chart that
shows only the candles leaves a reader guessing what the strategy saw. An indicator series is one
worker's outputs drawn the way every charting tool draws an indicator: one value per bar, on the
candles or in a pane of its own under them, with its scale, its reference lines and a legend. The
worker declares how its outputs are drawn; the run records the values; this document says what each
field of a served series holds, so a client can draw it.

**Routes**

```
GET /api/v1/drawing-types
```

The series themselves are served by the [recorded streams](/api/v1/docs/recorded-streams) routes:
each drawing worker is a row of a unit's timeline, and the window serves its values in the field
`indicator_series`, beside the state frames.

**What is not here:** the decision logic's own drawings — signals, regimes, notes — and every other
kind of drawing; they come later, under the same style vocabulary. A backtest's series are recorded
but not served yet, like the rest of a backtest's streams over time.

## The vocabulary as a schema

`/drawing-types` is open, like `/timeframes`: it states what a field means and nothing about a run.
It serves the JSON Schema of one served series entry, with every enumeration and the style
vocabulary in its definitions — generate your types from it rather than from this page.

```json
{"indicator_series": {"title": "IndicatorSeriesEntry", "type": "object",
                      "properties": {"layer": {"…": "…"}, "pane": {"…": "…"}, "…": "…"},
                      "$defs": {"DrawingRole": {"enum": ["negative", "positive", "warning", "annotation", "neutral"]},
                                "…": "…"}}}
```

It changes only when the server does; cache it the way [server](/api/v1/docs/server) says.

## One series, as the window serves it

An RSI in a pane of its own, three bars of the window. Every field is served — one left unset is
null, or empty for a list or an object — and the nulls and empties are left out here:

```json
{"layer": "worker:rsi_fast", "pane": "rsi_fast", "title": "RSI",
 "inputs": {"period": 14}, "timeframe": "M5",
 "price_format": {"type": "price", "precision": 2},
 "range": {"min_value": 0.0, "max_value": 100.0},
 "plots": [{"id": "rsi_value", "kind": "line", "style": {"role": "neutral"}}],
 "price_lines": [{"price": 70.0, "line_style": 2, "title": "70"},
                 {"price": 30.0, "line_style": 2, "title": "30"}],
 "filled_areas": [{"between": ["price_line:70", "price_line:30"],
                   "style": {"role": "annotation", "emphasis": "quiet"}}],
 "times": ["2026-12-01T10:00:00Z", "2026-12-01T10:05:00Z", "2026-12-01T10:10:00Z"],
 "data": {"rsi_value": [null, 41.2, 44.8]}}
```

Bollinger bands on the candles: three plots that switch together, the channel between the outer two
filled, and no `price_format` — the series is in price units and takes the symbol's own digits:

```json
{"layer": "worker:bollinger_main", "pane": "price", "title": "Bollinger",
 "inputs": {"period": 20, "deviation": 2, "ma_type": "sma"}, "timeframe": "M30",
 "plots": [{"id": "upper", "kind": "line", "style": {"role": "neutral", "series": 0}},
           {"id": "middle", "kind": "line", "style": {"role": "neutral", "series": 1, "emphasis": "quiet"}},
           {"id": "lower", "kind": "line", "style": {"role": "neutral", "series": 0}}],
 "filled_areas": [{"between": ["plot:upper", "plot:lower"],
                   "style": {"role": "annotation", "emphasis": "quiet"}}],
 "times": ["2026-12-01T10:00:00Z", "2026-12-01T10:30:00Z", "2026-12-01T11:00:00Z"],
 "data": {"upper": [2051.4, 2053.9, 2055.0], "middle": [2040.2, 2041.0, 2041.7],
          "lower": [2029.0, 2028.1, 2028.4]}}
```

## What the fields hold

| Field | Meaning |
|---|---|
| `layer` | the row in a Layers panel: `worker:<instance>`, or `worker:<instance>:<name>` for a further series of the same worker; its plots switch together. Unique within a unit — the window's `keys` names it |
| `pane` | `price` draws on the candles, on the price scale; any other value names a pane of its own under them — one pane per name, so two series naming the same pane share it |
| `title`, `inputs` | the legend: `RSI 14` is the title and the inputs' values. `inputs` holds the worker's period on its timeframe as `period`, then its plain parameters in the order it declares them |
| `timeframe` | the bars the values belong to — the series' own, which need not be the chart's. A worker that computes on two timeframes draws a series on each, each its own entry with its own `times` |
| `price_format` | the scale's labels: `type` (`price`, `percent`, `volume`), `precision` digits, `min_move` the smallest step. Null means the symbol's own price format |
| `range` | a fixed scale from `min_value` to `max_value`, such as RSI's 0 to 100; null lets the scale follow the values. Only a series in a pane of its own has one |
| `plots` | the outputs drawn, at least one; the first is drawn behind the others. `id` is the worker's output name and the key into `data` |
| `plots[].kind` | `line`, `histogram`, `area` or `baseline` |
| `plots[].options` | what a chart library needs beyond the kind: `line_type` and `line_style` as Lightweight Charts' own codes, `line_visible`, `point_markers_visible`, `base` (a histogram's zero), `base_value` (where a baseline splits) — each null where the library's default holds |
| `price_lines` | horizontal reference lines on the series' scale, with `line_style` and an optional `title` |
| `filled_areas` | an area between two edges: `plot:<id>` and `plot:<id>`, or `price_line:<price>` and `price_line:<price>` — never one of each. The price is written as the shortest decimal that reads back as the same number, never in exponent form and without a trailing `.0`: `70`, `70.5`, `0.00001234` |
| `times` | each bar's opening instant, in UTC, the same instant the bars route serves for that bar |
| `data` | per plot id, one value per entry of `times`; null where the worker had no value for that bar |

**Values are aligned with the bars, never filled.** A bar the worker had no value for — during its
warmup, or one no pass of the run computed on — is null, and is never left out; nothing is carried
forward. Draw a null as a gap.

**A run draws what it recorded, never what its worker declares today.** The series' meaning —
its title, its pane, its plots — is the copy the run kept when it started, in the stream's header. A
run recorded before this contract, one with the recording switched off, and one whose worker
declared nothing then have no series, even where the worker declares one now; a worker that changes
its declaration later leaves an older run's series as they were. Draw only the series the run's
timeline lists.

**A value belongs to the bar's close.** It is the value the worker computed on the bar's final state,
so a series records a bar only once it has closed; the bar still forming has no value yet.

## Style is a role, not a colour

A plot or area names what it means, and the viewer's theme picks the colour — one checked against
both themes and against red-green colour blindness.

| Field | Meaning |
|---|---|
| `role` | `negative`, `positive`, `warning`, `annotation` or `neutral` |
| `series` | tells several drawings of one role apart — `0`, `1`, `2` — and means nothing beyond that |
| `emphasis` | `strong`, `normal` or `quiet`, in place of a width or an opacity; null leaves it to the viewer |
| `color` | an explicit colour; it leaves the theme and its colour-blindness check, so a client may ignore it |

## Reading the series of a unit

- **Find them** in the unit's [timeline](/api/v1/docs/recorded-streams): one row per drawing worker,
  `stream_kind` `indicator_series`, `stream_name` the worker instance, `record_time_field`
  `bar_time` and `cadence_timeframe` the finest timeframe the worker draws on.
- **Read them** from the window: `indicator_series` holds one entry per series of every drawing
  worker, each with the bars the window covers. The window's `limit` counts these bars together with
  the state frames, so a page holds a stretch of every stream at once.
- **Follow a running session** as the recorded streams document says: a series gains a bar each time
  its timeframe closes one.
