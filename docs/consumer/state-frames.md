# How a running session stands

Every report section of a session is written when it ends, so while it runs, its equity, its open
positions, its resting orders and what it last decided are on none of them — and a session that is
running and one that died look the same from outside. A session records its state as it goes, a
frame at a time. This route serves the latest frame of each unit of a run, how old it is, and
whether the session is still recording: the check-in on a running session.

**Route**

```
GET /api/v1/reports/runs/{run_id}/state-frames/latest
```

**What is not here:** the frames between the first and the latest; every step of every order — the
[order events](/api/v1/docs/order-events); whether the session's process is alive and what it holds
from outside — the [activities](/api/v1/docs/activities); the report sections a session writes when
it ends, such as the [portfolio](/api/v1/docs/portfolio).

Reached with a grant on the `reports` surface — `reports:<run_id>`, or `reports:*` for every run.

**Ask it once a cadence, never faster.** A frame is written once per period of
`cadence_timeframe` — `cadence_s` seconds — and at a few moments between; asking more often returns
the same frame. The run list names the stream under `stream_files` (`state_frames.jsonl`) from the
moment the session opens it, so a client can tell which runs to ask.

## An answer

```json
{"key": ["unit_name"],
 "served_at": "2026-11-11T07:59:12Z",
 "units": [
   {"unit_name": "eth_daily",
    "cadence_timeframe": "M1",
    "cadence_s": 60.0,
    "written_at": "2026-11-11T07:58:31Z",
    "age_s": 41.0,
    "recording": "current",
    "truncated_tail": false,
    "first_frame": {"written_at": "2026-11-01T00:00:14Z",
                    "process_memory": {"rss_bytes": 402653184, "private_bytes": 361758720}},
    "outcome": null,
    "frame": {"frame_time": "2026-11-11T07:58:31Z", "written_at": "2026-11-11T07:58:31Z",
              "written_for": "cadence", "cadence_slot": "2026-11-11T07:58:00Z",
              "ticks_processed": 412977, "symbol": "ETHUSD", "account_currency": "USD",
              "last_price": 2412.5, "balance": 248.10, "balances": {"USD": 248.10, "ETH": 0.1},
              "equity": 489.35, "unrealized_pnl": 1.25,
              "open_positions": [], "active_orders": [],
              "safety_blocked": false, "safety_drawdown_pct": 0.4, "market_stale": false,
              "feed_stale": null, "reconcile_clean": true,
              "worker_outputs": {"rsi_main": {"rsi": 41.2}},
              "decision_action": "FLAT", "decision_outputs": {"signal_strength": 0.12},
              "closed_bars": {}, "order_id": null,
              "process_memory": {"rss_bytes": 431225856, "private_bytes": 388235264}}}]}
```

`key` names what makes one row of `units` unique. A session is one unit; a run of several units
lists each.

`cadence_timeframe` is the unit's cadence as a timeframe — `M1` unless its profile says otherwise:
one frame per period of it on the session's own clock, so every cadence frame lies on a bar
boundary — and `cadence_s` is the same in seconds. A session records at the cadence it started
with, whatever the setting says later.

## The frame

What the session held and decided at one moment: the account — its balance, at spot every currency
it holds, its equity and the unrealised P&L, valued at `last_price`, the mid; the open positions and
the resting orders, as the same rows the [portfolio](/api/v1/docs/portfolio) and the
[pending orders](/api/v1/docs/pending-orders) serve; the circuit breaker; whether market data or a
signal is stale; whether the latest reconciliation with the venue was clean; every output of every
worker and of the decision; and the memory the session's process held.

- `frame_time` is the session's own clock: the time of the market data it was processing. In a
  session replaying recorded data that is the replayed time, months behind.
- `written_at` is when the frame was written, on the machine's clock. The age is measured from it.
- `written_for` says why the frame was written: `cadence` (`frame_time` entered a new period of
  the cadence's timeframe; `cadence_slot` names the period by its opening),
  `bar_close` (a bar the workers read closed; `closed_bars` names it by its opening), `order` (the
  decision sent an order; `order_id` names it) or `shutdown` (the session's last frame, at the state
  its report describes).
- **null means not checked**, never a default: `feed_stale` is null when the session reads no
  signal, `reconcile_clean` when it runs no reconciliation — or none has reached the venue yet, or
  the latest could not — and `last_price` and `equity` before the first price.
- `process_memory` is on every frame of a live session; a replayed session records it now and then.

## How current it is — `age_s` and `recording`

`age_s` is how many seconds ago the latest frame was written; null when the frame's time lies in
the future of this server's clock — a clock stepped back. `recording` reads it:

| `recording` | when | what it means |
|---|---|---|
| `current` | the latest frame is younger than three of its cadences | the session is recording |
| `stopped` | older than that, and it is not the last one | the recording stopped without an end: the session hangs, or its process died — the [activities](/api/v1/docs/activities) tell which |
| `ended` | the latest frame is the one written at the end | the session ended — after a regular stop and after an emergency alike |
| null | a session replaying recorded data, before its end | its frames come at replay speed, so their age says nothing about whether it runs |

How many cadences a frame may age is a setting of the server, `api.state_frames.stopped_after_cadences`
(three). At a cadence of a minute that is three minutes, longer than a session takes to report a
stall of its own loop.

**How a session ended** is in `outcome`, exactly as the [warnings and errors](/api/v1/docs/warnings-errors)
serve it: the graded `run_outcome`, `shutdown_mode`, whether the operator interrupted it, and the
emergency's reason. The session writes it in its report phase, seconds after its last frame, so
`outcome` is null until then — and stays null when the process died in between.

## `first_frame` — the growth since the start

The unit's first frame, read from the other end of its stream: when it was written and what the
process held then. Beside the latest frame's `process_memory` it gives the growth over the session's
life in one answer — `private_bytes` is what a leak grows. Two points are not a series: a climb that
fell back reads as flat.

## `truncated_tail`

True when the stream's last line was being written as it was read — or the session died while
writing it. That line is left out and the frame before it is served.

## When there is no frame

| Status | `error` | meaning |
|---|---|---|
| 404 | `run_not_found` | no such run in the run index |
| 404 | `state_frames_not_recorded` | the run records no state frames: a session from before they were recorded, one with the recording switched off, or a backtest |
| 404 | `state_frame_not_written_yet` | the session has opened its stream but not written its first frame yet — ask again after one cadence |
| 409 | `artifact_unreadable` | the stream, or the end classification, exists but cannot be read |

All codes are in the [error table](/api/v1/docs/errors).
