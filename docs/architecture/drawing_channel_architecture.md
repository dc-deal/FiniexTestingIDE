# The Drawing Channel — what a worker shows on a run's chart

A worker computes an RSI, a set of Bollinger bands, a MACD — and until now nobody could see those
values on the chart beside the candles they were computed from. A reader had to guess from a log
whether the strategy's indicator looked the way it expected. The drawing channel lets a worker show
its outputs on a run's chart, as the indicator panes every charting tool has: one value per bar, on
the candles or in a pane of its own, with its scale, its reference lines and a legend.

A worker writes no drawing code. It **declares** once how its outputs are drawn; the run records the
values it computes anyway; the API serves them to the viewer, which draws them.

**What is not here:** the decision logic's own drawings — signals, regimes, notes — and the other
drawing primitives (lines, markers, bands, labels, segments) with their cards and anchors; the
algo-event stream; a worker drawn on a free chart with no run. They wait for the drawing
vocabulary's research in #379. The fields a client reads are in the served document
[indicator series](../consumer/indicator-series.md); how a run's streams are served over time is
[recorded streams](../consumer/recorded-streams.md).

## A worker declares its series

`AbstractWorker.get_indicator_series()` is a class method beside `get_metadata()` and
`get_output_schema()`. It returns the worker's series — none by default, so a worker draws only
what it declares. Each series is an `IndicatorSeriesDeclaration`
(`python/framework/types/chart_types/indicator_series_types.py`):

```python
@classmethod
def get_indicator_series(cls) -> Tuple[IndicatorSeriesDeclaration, ...]:
    return (IndicatorSeriesDeclaration(
        pane=IndicatorPane.OWN, title='RSI', price_format=PriceFormat(precision=2),
        range=ValueRange(min_value=0, max_value=100),
        plots=(Plot(id='rsi_value'),),
        price_lines=(PriceLine(price=70, title='70'), PriceLine(price=30, title='30')),
        filled_areas=(FilledArea(between=('price_line:70', 'price_line:30')),)),)
```

- **The producer states the meaning, the viewer chooses the presentation.** Where the series stands
  (`pane`), its scale (`range`, `price_format`), its reference lines, which outputs belong together
  and the values are the worker's to state. Colours, widths and pane heights are the viewer's: a
  plot carries a style *role* — `negative`, `positive`, `warning`, `annotation`, `neutral` — and the
  viewer's theme picks a colour that holds up in both themes and for red-green colour blindness.
- **A plot draws one output**, named by its id exactly as `get_output_schema()` names it. The
  outputs that belong together are one series — MACD's line, signal and histogram switch together.
- **`price_format` left out means the price's own**, as the symbol's digits give it: a series on
  the candles, or one in price units such as MACD.
- **A further series** of the same worker names its own `layer_name` and is switched apart from
  the first.

The CORE workers that draw declare their series in their own class; the ones that draw nothing —
a signal worker, which has no bars to give a value to, and the test probes — are listed with their
reason in `CORE_WORKERS_DRAWING_NOTHING` (`python/framework/factory/worker_factory.py`). A test holds
every CORE worker to exactly one of the two. A user's worker declares the same way; the user guide
says how ([worker naming](../user_guides/worker_naming_doc.md#drawing-a-workers-outputs)).

## The declaration is checked when the worker is created

What a declaration can be wrong about on its own, its types refuse while it is built: a filled area
between a plot and a price line, a plot declared twice, a fixed range on the candles' pane, a name
holding `:` or `/`. What needs the worker is checked when the factory creates it — for a CORE worker
and a user's file alike, before the run starts (`validators/indicator_series_validator.py`): every
plot draws an output the worker declares, that output is a number, and a further series names its
layer. A wrong declaration stops the run with `IndicatorSeriesDeclarationError`, naming the worker
and what to fix, rather than leaving a pane missing after it.

## The vocabulary is served as a schema

`GET /api/v1/drawing-types` serves the JSON Schema of a served series entry, the style vocabulary in
its definitions, so a client generates its own types from it rather than copying a description. It
is open like the timeframes: what a field means is the application's own static declaration.

## The indicator series stream

A run that records its series writes one stream per drawing worker instance, one line per closed bar
of each timeframe the worker draws on — `io/indicator_series_{worker}.jsonl` in a session, sealed per trading day
like its other streams, and `io/indicator_series_{scenario}/{worker}.jsonl` per scenario in a
backtest, whose header names the stream `scenario_indicator_series`. A scenario's files lie in a
folder of their own, so the session's pattern never takes one for a session's, and a scenario or a
worker name holding `_` still parts cleanly. The header line carries the
series' meaning — every series of the worker as the run draws it: its declaration, the layer and
pane it is drawn in, the legend's inputs and its timeframe — and the finest of those timeframes as
`cadence_timeframe`. Each record holds a bar's opening instant, its timeframe and one value per plot
of the series drawn on that timeframe. A worker on two timeframes, such as `CORE/tide_and_wave`:

```
{"record_kind": "header", "stream": "indicator_series", "schema_version": 1, "run_id": "…",
 "unit_name": "my_session", "worker": "tide_wave", "cadence_timeframe": "M5",
 "series": [{"title": "Wave", "layer": "worker:tide_wave", "pane": "tide_wave", "timeframe": "M5", …},
            {"title": "Tide", "layer": "worker:tide_wave:tide", "pane": "tide_wave:tide",
             "timeframe": "M30", …}]}
{"bar_time": "2026-12-01T10:25:00+00:00", "timeframe": "M5", "values": {"wave_rsi": 41.2}}
{"bar_time": "2026-12-01T10:00:00+00:00", "timeframe": "M30", "values": {"tide_histogram": 0.84}}
{"bar_time": "2026-12-01T10:30:00+00:00", "timeframe": "M5", "values": {"wave_rsi": 44.8}}
```

A bar the worker computed nothing on is `null`, and a bar is never left out, so the values stay
aligned with the bars.

## The recorder — which value stands for a closed bar

`IndicatorSeriesRecorder` (`python/framework/process/indicator_series_recorder.py`) is one class for
both pipelines, like the state-frame recorder beside it. Each runner calls it right after its state
frame — after the pass, before the end check — with the timeframes the pass saw close and the bars
still forming. It reads the outputs the workers already computed and changes nothing: the orders of
a run are the same with the recording on and off, and a test holds that on a backtest that trades.

The value written for a closed bar is the one the worker computed on that bar's final state:

- **A worker on the LIVE basis** computes on the forming bar, so on the pass that closes a bar its
  newest output already holds a tick of the next one. Its value for the closed bar is the one from
  its last pass inside that bar, which the recorder keeps from pass to pass.
- **A worker on the BAR_CLOSE basis** computes on the pass that closes the bar, on completed bars, so
  that pass's output is the value.
- **A bar no pass computed on** — during the warmup, or one that closed between two passes — is
  `null`. Nothing is carried forward.

**A series draws on the timeframe it declares** — `timeframe_position`, the position in the worker's
`periods`, 0 by default, the first. A worker that computes on more than one timeframe gives each
further one a series of its own, with its own `layer_name`, so it is a layer of its own that a reader
switches apart. The factory refuses a position the worker's `periods` do not give it, when the worker
is created. The recorder keeps each timeframe's bars apart: a pass that closes an M5 and an M30 bar
at once writes a record for each, and the value for a LIVE worker is its last pass inside each bar.
`CORE/tide_and_wave` is the example: the first two screens of Elder's Triple Screen as an indicator,
the wave's RSI on the first timeframe and the tide's MACD histogram on the second.

The recording is switched per run: `execution_config.indicator_series.enabled` for a backtest's
scenario, `indicator_series.enabled` in an AutoTrader profile, both off by default in
`configs/app_config.json`. On, it also makes the loop read which timeframes closed on every pass, as
the state frames do. A worker that declares a series but has no bars — no timeframe to give a value
to — is said in the log and left out; a run where no worker draws opens no stream. The run's
footprint expects the stream when the switch is on and a worker draws. A backtest built from a
session's records (#512) records its series exactly when the session did.

A session's series is served through the recorded-streams routes: each stream is a row of the unit's
timeline, and the window serves the series beside the state frames, as one entry per series of
every drawing worker, keyed by its `layer`, with parallel `times` and `data` arrays. A chart draws a
run's series only from what the run recorded: a run recorded before the drawing channel, with the
recording off, or with a worker that declared nothing then, has none, whatever the worker declares
today — and a worker that changes its declaration later leaves the series it drew before as they
were, since the stream's header keeps the declaration the run drew with. A backtest's streams are not served yet — that comes with the
backtest's state frames (#579 part B).
