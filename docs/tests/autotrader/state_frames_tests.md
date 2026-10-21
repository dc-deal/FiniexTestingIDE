# State Frames Tests

**Suite:** `tests/autotrader/state_frames/` · **Mark:** `autotrader` (unit + integration)

A live session's state over time is gone once the moment passes — the equity, the open positions,
the worker outputs and the decision at that moment. A backtest of the same period can be run again;
the session cannot, so it records its own side while it runs (#579 part A): a state frame in
`io/state_frames.jsonl` on a cadence of the canonical clock, at a closed bar, at an order, and once
at its end. This suite holds when a frame is written, what it holds, and that the last one agrees
with the session's own report.

Not here: the appender the frames are written through — `tests/framework/reporting/`
(`test_run_stream_appender.py`); serving the latest frame (#611); the simulation's frames (#579
part B).

## Tests

| File | Group | Checks |
|---|---|---|
| `test_state_frame_recorder.py` | `TestTheCadence` | slots on a grid of the canonical clock, each filled once · a clock that steps back begins the grid again without a frame · a replay's heartbeat (which reads the wall clock) does not drive the cadence, a live heartbeat does on a quiet feed |
| | `TestTheEvents` | an order is a frame of its own, naming the order, even inside a filled slot · a closed bar names the bar by its opening · an event fills a due slot, so one pass writes at most one frame |
| | `TestWhatAFrameHolds` | every output as a plain value, a value not defined yet (NaN) as null · the account valued at the mid · before a tick the account is unvalued, never guessed · a health nobody checked is null, one that was checked is said · a reconciler with no verdict yet — before its first cycle, after one that could not reach the venue — checked nothing |
| | `TestTheMemory` | a live session reads the process's memory on every frame, also when two frames lie a little under one interval apart · a replay reads it at most once per cadence interval of the monotonic clock, and always in the last frame |
| | `TestAFrameThatCannotBeBuilt` | said once, skipped, the recording goes on, and the count is said at the end |
| | `TestTheEnd` | the last frame is written and the stream closed; nothing after it |
| `test_state_frames_session.py` | `TestTheRecord` | a real mock session: the header · the cadence on the replayed tick time · one frame per pass · every closed bar named by its opening on its own grid · the order recorded at its moment, by an id the session's order history knows · null where a mock session checks nothing |
| | `TestTheLastFrameAgreesWithTheReport` | balance, equity, price and the open position rows of the last frame equal the session's `portfolio.json` — the same moment, the same price, the same rows |
| | `TestSwitchedOff` | `state_frames.enabled: false` writes no stream |

## Why the agreement test matters

The frame and the report are two records of one account. If the last frame said one equity and the
report another, a reader comparing the live run with its backtest would not know which one the
divergence is measured from. The frame takes its position rows from the same builder as the report
and is written at the point the report reads — after the session's orders are finished, at the same
price — so the test pins a property the construction promises.

## The profile

`configs/autotrader_profiles/mock/state_frames_test.json` — the mock session's strategy over
02:19–08:00 of a replayed day: a frame every minute, an M5 and an M30 timeframe that close bars, and
one buy around 06:12 that is still open at the end.

## Run

```
pytest tests/autotrader/state_frames/ -v
```
Or the launch entry `🧩 Pytest: State Frames (#579)`; `🧪 AutoTrader: State Frames` runs the
profile with the display.
