# A unit's recorded streams over time

A run's report shows how it ended; what it held and decided on the way there is recorded as it
goes, one record at a time — a session's state frames, written once a minute and at every order,
and, where its workers draw, each worker's indicator series, one value per closed bar. These two
routes serve those records over time, per unit of a run: a timeline to draw the axis from, and a
window to read the records of all its streams a page at a time. A finished run and a running session answer
alike — the records the session is still writing are part of every answer.

**Routes**

```
GET /api/v1/reports/runs/{run_id}/units/{unit_name}/recorded-streams
GET /api/v1/reports/runs/{run_id}/units/{unit_name}/recorded-streams/window?from=&to=&limit=
```

**What is not here:** the latest frame with its age and whether the session still records, which
is the [state frames](/api/v1/docs/state-frames) route — the timeline carries the same verdict; the
steps of every order — the [order events](/api/v1/docs/order-events); what the fields of a drawn
series mean — the [indicator series](/api/v1/docs/indicator-series); the report sections a run
writes when it ends.

Reached with a grant on the `reports` surface — `reports:<run_id>`, or `reports:*` for every run.
A unit is a session in an AutoTrader run, named as its streams name it (`unit_name` on the state
frames route). A backtest's streams over time are not served yet, not even the state frames and
series its scenario can record.

## The timeline

One row per stream the unit records — its state frames, and one indicator series per drawing worker
— with how many records it holds and the time of its first and latest one, read without the records. **Ask it every second or two** to
follow a running session: when `record_count` or `last_record_time` moves, read what is new from
the window.

```json
{"run_id": "20261201_010500_ab12cd34",
 "unit_name": "my_session",
 "served_at": "2026-12-14T10:15:02.114523Z",
 "recording": "current",
 "key": ["stream_kind", "stream_name"],
 "streams": [
   {"stream_kind": "state_frames",
    "stream_name": "state_frames",
    "record_time_field": "frame_time",
    "record_count": 18802,
    "first_record_time": "2026-12-01T01:05:00.412318Z",
    "last_record_time": "2026-12-14T10:15:00.087204Z",
    "cadence_timeframe": "M1",
    "cadence_s": 60.0,
    "truncated_tail": false},
   {"stream_kind": "indicator_series",
    "stream_name": "rsi_fast",
    "record_time_field": "bar_time",
    "record_count": 3842,
    "first_record_time": "2026-12-01T01:05:00Z",
    "last_record_time": "2026-12-14T10:10:00Z",
    "cadence_timeframe": "M5",
    "cadence_s": 300.0,
    "truncated_tail": false}]}
```

| Field | Meaning |
|---|---|
| `recording` | whether the session still records — `current`, `stopped` or `ended` — the verdict the state frames route gives; null before the first frame, for a replay before its end, and for a unit that records no state frames |
| `stream_kind` | what the stream is — `state_frames` or `indicator_series`; more kinds join as the run records more |
| `stream_name` | which stream of its kind — a kind a unit records several of tells them apart by name, so a row is keyed by both (`key`); a unit's state frames are one stream, `state_frames`; an indicator series is named for its worker instance |
| `record_time_field` | the field of each record its time is read from — `frame_time` for state frames, the session's own clock; `bar_time` for an indicator series, the opening instant of the bar its value belongs to |
| `record_count` | how many records the stream holds; 0 when its header is written and no record yet |
| `first_record_time`, `last_record_time` | the time of its first and latest record; null when it holds none |
| `cadence_timeframe`, `cadence_s` | the cadence it is written on — for an indicator series the finest timeframe its worker draws on, a record at each bar that closes; null for a stream written per event |
| `truncated_tail` | a last line cut off mid-write was left out |

## The window

The unit's records whose time lies in `[from, to)`, every stream's merged by their time, oldest
first, at most `limit` of them — more only where one millisecond holds more (below). Within one
instant a state frame comes before an indicator series' bar.

| Parameter | Meaning |
|---|---|
| `from` | the first instant read, ISO-8601 (UTC when no offset is written); left out, the window starts with the stream |
| `to` | the first instant no longer read; left out, the window ends after the newest record |
| `limit` | the most records served, counted over every stream, 1 to 2000 — exceeded only by a millisecond holding more (below); omitted, 2000. Outside that range it is refused with `invalid_limit`, never clamped |

```json
{"run_id": "20261201_010500_ab12cd34",
 "unit_name": "my_session",
 "served_at": "2026-12-14T10:15:04.020871Z",
 "window": {"from": "2026-12-03T00:00:00Z", "to": "2026-12-10T00:00:00Z"},
 "matched": 10080, "returned": 2000, "limit": 2000, "truncated": true,
 "next_from": "2026-12-04T09:20:00.207000Z",
 "keys": {"state_frames": ["record_no"], "indicator_series": ["layer"]},
 "state_frames": [
   {"record_kind": "frame", "frame_time": "2026-12-03T00:00:00.207118Z", "written_for": "cadence",
    "…": "every field of a state frame, as the state frames route describes it",
    "record_no": 2878}],
 "indicator_series": [
   {"layer": "worker:rsi_fast", "pane": "rsi_fast", "title": "RSI", "timeframe": "M5",
    "…": "the series' meaning, as the indicator series document describes it",
    "times": ["2026-12-03T00:00:00Z", "2026-12-03T00:05:00Z"],
    "data": {"rsi_value": [47.3, 51.9]}}]}
```

- **`window`** says which instants the answer covers — as asked, or, where a bound was left out,
  resolved from the records the window holds: `from` the first one's millisecond, `to` the
  millisecond after the newest one's. A bound left out stays null when the window holds no record.
- **`matched`** counts the records in the window, **`returned`** the ones served — both over every
  stream, a series' bars counted one each.
- **`indicator_series`** holds one entry per series of every drawing worker, with the bars of the
  page in parallel arrays — `times` and, per plot, `data`. An entry is keyed by its `layer`, and it is
  there with empty arrays when the page holds none of its bars.
- **`record_no`** is a record's position in the unit's stream, counted from 1. It identifies the
  record in every answer — two frames can share an instant — and the newest one equals the
  timeline's `record_count`.
- **A page never ends inside a millisecond.** When the limit would cut a millisecond, the page ends
  before it, and `next_from` is its start. A millisecond holding more records than `limit` is served
  whole, so `returned` can exceed `limit`.
- **A page never reaches past a record it left out.** Each stream is read up to `limit` records; a
  page ends before the first record any stream left out, so a stream with many records in the window
  can end a page before `limit` is reached.
- **`next_from` is set only when the page left records out** (`truncated`). Ask the next page with
  `from` set to it and the same `to`: no record is repeated or skipped.

## Seeking and following

- **Seek** to an instant with `?from=<instant>&limit=1`: the first record at or after it.
- **Follow** a running session: when the timeline moves, ask the window from the start of the
  millisecond of the newest record you hold, `to` left out, and drop the rows you already hold — a
  state frame by `record_no`, a series' bar by its `layer` and its time.

## Errors

| Status | `error` | When |
|---|---|---|
| 400 | `invalid_limit` | `limit` below 1 or above 2000 |
| 400 | `invalid_range` | `from` is not earlier than `to` |
| 400 | `invalid_timestamp` | `from` or `to` is not ISO-8601 |
| 404 | `run_not_found` | no run of that id |
| 404 | `streams_not_recorded` | the run records no stream over time — neither state frames nor an indicator series; a backtest's are not served yet |
| 404 | `unit_not_found` | the run records no unit of that name |
| 404 | `state_frame_not_written_yet` | the session opened its streams and none has written its header yet |
| 409 | `artifact_unreadable` | a stream cannot be read |
