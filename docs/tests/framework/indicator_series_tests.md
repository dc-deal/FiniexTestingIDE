# Indicator Series Tests

**Suite:** `tests/framework/indicator_series/` · **Mark:** `framework` (unit + one real backtest)

A worker declares how its outputs are drawn on a run's chart, and the run records one value per
closed bar of its timeframe (#379). A wrong declaration found only when the viewer draws nothing is
found too late, and a recorder that writes the value of the wrong bar draws a curve that does not
match the candles beneath it. This suite holds the declarations to the workers and the recorder to
the bar each value belongs to — and that switching the recording on changes no order.

Not here: serving the series over time — `tests/framework/api/test_recorded_streams_routes.py`
(*TestTheIndicatorSeries*, in [API endpoint tests](api_endpoint_tests.md)); the `/drawing-types`
route — `test_api_endpoints.py` and `test_api_auth.py`; whether a run writes the stream and its day
record where the run store declares them — the [run entry census](run_entry_census_tests.md).

## Tests

| File | Group | Checks |
|---|---|---|
| `test_indicator_series_declarations.py` | `TestEveryCoreWorkerAnswers` | every CORE worker declares a series or is listed as drawing nothing, never both · the list names registered workers only · every declaration passes the check · RSI stands in its own pane from 0 to 100, Bollinger on the candles with its channel filled |
| | `TestADeclarationRefusesWhatCannotBeDrawn` | a fixed range needs a pane of its own · a filled area never mixes a plot and a price line, and names edges of its own series · a plot is declared once, a series draws at least one · a name holds no `:` and no `/` · a range has its bottom below its top · the price format is the symbol's unless stated · a price line is named by its exact price, so two lines apart in the seventh digit are two lines |
| | `TestAWorkerIsCheckedWhenCreated` | a user worker's file is checked like a CORE worker: a plot of an output it lacks is refused, a plot of a number accepted · a series on a timeframe the worker's `periods` do not give is refused, and accepted with two · a further series names its layer · a text output is not drawn |
| `test_indicator_series_recorder.py` | `TestTheValueThatStandsForAClosedBar` | a LIVE worker records its last value inside the bar, never the closing pass's · a BAR_CLOSE worker records the closing pass · a pass that closes none of the worker's bars writes nothing |
| | `TestABarWithoutAValueIsNull` | a bar before the first value, and a value that is no number yet, are null · bars that closed between two passes, or before the first record, are null, never left out · a warmup bar is never recorded |
| | `TestTheStreamThatIsOpened` | the header names the layer, its pane, its inputs and its cadence · a worker on two timeframes records each series on its own closes, each record naming its timeframe · a run where no worker draws opens no stream |
| | `TestOnARealBacktest` | the fixture set run twice: its orders and trades are the same with the recording on and off, and it trades, so the comparison holds something · switched off, no series is written · its files are the scenario's member, never a session's · each of its drawing workers — the tide-and-wave worker on M5 and M30 — records every bar of each timeframe from the first tick's to the last one a tick closed, none left out, and holds values |

## Why the orders test matters

The recorder reads what the pass already computed and is meant to change nothing — it runs after
the pass, beside the state frame. But switching it on also makes the loop read which timeframes
closed on every pass. A recording that moved a single order would make every run that draws its
series a different run from the one that does not; the test compares every order event and every
trade, on a set whose thresholds are loosened so it trades.

## The scenario set

`configs/scenario_sets/backtesting/indicator_series_test.json` — `CORE/simple_consensus` with RSI on
M5, Bollinger on M30 and OBV on M5 over ten hours of ETHUSD, and beside them `CORE/tide_and_wave` on
M5 and M30, the series recorded. Its thresholds are
loosened so it places orders; the other tests read stand-ins for the run's workers and bars.

## Run

```
pytest tests/framework/indicator_series/ -v
```
Or the launch entry `🧩 Pytest: Indicator Series (All)`; `🧪 Simulation: Indicator Series` runs the
scenario set with its full log.
