# Day Records — a Session's Streams, Sealed per Trading Day

A session appends its streams line by line — such as its order events, its closed trades, its state
frames and a strategy's diagnostics rows — so a session that dies keeps every line up to the moment
it died. Which streams are sealed is declared by the run store, stream by stream. Over a thirty-day session one file per stream
would grow for thirty days, and answering "what happened on day 12" would mean reading all of it. At
each trading-day boundary the session therefore **seals** the day: the lines the stream's file holds
move into an immutable parquet file, the **day record**, and the file starts the next day afresh. A
reader gets the stream back as one, whichever way it is split.

Not in this document: what each stream's lines hold (the order events in
[order_event_stream.md](order_event_stream.md), the state frames in
[live_telemetry_architecture.md](live_telemetry_architecture.md)), the ledger row a sealed day books
([accounting_periods.md](accounting_periods.md)), and how long day records are kept — nothing deletes
them yet. A backtest writes its streams at its end and never seals one.

## What a session's run directory holds after two boundaries

The stream's own file keeps its name and holds the running trading day. Each sealed day is a folder
named by the date the day opened — the same name the session's day logs carry — and inside it every
stream lies at the path it has in the run, with `.parquet` for `.jsonl`:

```
<run>/io/order_events.jsonl                  the running day; its header names it:
                                             {"record_kind": "header", "stream": "order_events", ...,
                                              "trading_day": "2026-11-03"}
<run>/io/state_frames.jsonl
<run>/diagnostics/entry_signals.jsonl
<run>/days/20261101/io/order_events.parquet  sealed, never written again
<run>/days/20261101/io/state_frames.parquet
<run>/days/20261101/diagnostics/entry_signals.parquet
<run>/days/20261102/io/order_events.parquet
...
```

A day folder therefore holds one whole trading day of the session. The run store declares each of
these files ([store_entry_members.md](store_entry_members.md)); the session's footprint expects them
once a trading day ended while it ran.

## How a day is sealed

The seal runs where the booking period of the day is sealed — in the tick loop's boundary check,
before the first event of the new day is applied, right after the day's ledger row was written. So
what the first tick of a day writes is already in the new day's file. Every stream the session opened
through its run writer is sealed in the same pass, one after the other:

```
close the file ──► read it, write the day record ──► replace the file by a fresh one
                   (a temporary file, renamed)       holding only the next day's header
```

- **The stream's file is only read while the day record is written**, and the fresh file replaces it
  in one step, the way every index and ledger fragment is replaced. A reader opens either the old
  file or the new one.
- **The day record is written durably** — its bytes forced to the disk before its rename, the
  rename after it — because from the fresh file on it is the day's only copy, and a file renamed
  to a new name can otherwise come back empty after a power loss. The ledger fragment is written
  the same way. It adds a few milliseconds per seal.
- **Writing, sealing and closing a stream take one lock.** The tick stream is written from the
  socket's own thread, which must never write into the middle of a seal.
- **A check that comes several days late seals once.** The loop did not run in the days it passed
  over, so nothing was written in them; they get no day record.
- **A failure never ends the session.** A day that cannot be sealed stays in the file and is sealed
  with the next one — said on the session's channel at each boundary that fails. So does a day whose
  file cannot be started afresh: the file was not replaced and still holds the whole day, so the day
  record just written is removed again and the stream goes on being written. Only a file replaced
  but not reopened stops that stream, the way a failed write does; everything up to the boundary is
  sealed.
- **A line is filed under the day in which the loop's check found it.** The tick stream is written
  from the socket's own thread, so a line it writes between the boundary and the check that notices
  the boundary is sealed with the day that ended. Its own time says when it happened; a time window
  reads it by that.
- **A session killed between the day record and the fresh file** leaves both, holding the same
  lines. The reader reads them once (below); nothing repairs the directory, since a restart is a new
  run.

**What it costs**, measured 2026-10-27 on the development container with
`python/experiments/cost_probes/probe_run_record_cost.py`, the durable write included: a day of 1,440
records of about 4 KB — a day of state frames at the default cadence of a minute — is sealed in about
100 ms on a local disk and in about 190 ms on the container's drive behind its bridge; a day of ten
records in about 6 ms. Most of it is parsing the day's lines. It is paid once a day, in the pass that
seals the day, and it is measured again on a server.

The session says each seal in one line:

```
🗄️ Day records sealed for 2026-11-02 — order_events 31 · trades 1 · state_frames 1,440 (0.21 s)
```

## What a day record holds

Every line exactly as the stream's file held it, and beside each line what a reader selects by
without parsing it:

| Column | Type | Holds |
|---|---|---|
| `line_no` | int32 | the line's number in the day's file, the header being line 1 |
| `record_time` | timestamp (µs, UTC), nullable | the line's own time — the first of the stream's declared time fields it carries |
| `record_kind` | string, nullable | the line's `record_kind`, or its `record_plane` |
| `line` | string | the line as written |

Because the line is kept as written, sealing a day cannot change a report, a route or the order
events a session reads back at its end. The footer of the file says what the day holds: the stream,
the version of its line shapes, the run, the trading day, the record count, the times of its earliest
and latest record, the header line the day's file opened with, and whether a last line cut off
mid-write was left out. The version of the day record's own layout stands there too.

A stream declares its time fields in the run store's declaration: the order events their
`event_time` on the canonical clock, the state frames their `frame_time`, the trades their
`exit_time`, the tick stream `lost_at` or `received_at` by the kind of line. Diagnostics rows carry
whatever columns their strategy chose, so they declare none, and a time window reads them by day.

## How a stream is read

The run directory's reader reads a stream as one (`RunDirReader.read_stream`): the sealed days, oldest
first, then the stream's own file. These rules keep that exact while a session seals underneath a
reader:

- **The file is read first, the days are listed after it.** A seal that happens in between has
  either sealed the day the reader already holds — and the next rule drops it — or it has not, and
  the day is still in the file.
- **A file whose day is already sealed is left out.** Its header names its day; the session's first
  file names none and counts as sealed once any day is.
- **A day record gone by the time the read asks for it was taken back.** A seal whose fresh file
  could not be started removes its day record again, and the day's lines are still in the file the
  read began with — so the day is read from there, as if it had never been sealed.

A read can be narrowed to a time window: a day whose records all lie outside it is not opened, and a
record without a time is read with its day. The two ends of a stream — its header, first and latest
record, what the state-frame check-in polls — come from the file's two ends, and right after a seal
from the newest day's last line. A stream's overview — what a unit's timeline is served from — lists
each day with its record count and the times of its first and last record from the day's footer, and
reads no record but the stream's two ends. A sealed day never changes, so the reading
process keeps what its footer and its two end lines say by the file's path, size and modification
time. A read asks each day record for its status once — two calls per sealed day with its listing —
and answers every question it has of the day from that. Once read, the two ends cost a listing of the
days and the two ends of the stream's file; the overview costs the same plus one read of the running
file, whose records it counts by their line ends — at most one trading day of them, a few megabytes
at the default frame cadence. Measured on a stream thirty days in with
`python/experiments/cost_probes/probe_recorded_stream_read_cost.py`: a unit's timeline 1.7 ms on a
local disk and about 210 ms on this container's bridged drive, where each status call costs
milliseconds — before each day was asked once, 3.2 ms and 1,056 ms.

**A page** (`RunDirReader.read_stream_page`) reads a window up to a limit — what a unit's window over
the API serves (#580). A stream's records come in the order of their time, because its writer stamps
them from a clock that only moves forward, so a page is a run of consecutive records and the read
stops at the day that fills it; the days after it are only counted, a whole day inside the window
from its footer, a day the window ends in from its time column. Three rules make walking a stream
page by page meet every record once:

- **A record is numbered by its position in the whole stream**, from 1: the sealed days' footer counts
  before it, then its place. A record keeps its number when its day is sealed, so the number
  identifies it in every answer.
- **A page never ends inside a millisecond.** The part of a millisecond the limit would cut moves to
  the next page, which starts there; a millisecond holding more records than the limit is served
  whole. A client that keeps time in milliseconds asks for the next page from where the last one
  ended and neither repeats nor skips a record.
- **A page says what it left out**: how many records the window holds, and where the rest begins —
  set only when the page left some out.

A page holds only records that carry their time. One without has no place in the order a page is
read in, and makes the stream unreadable by pages rather than appear on every page whose window
touches its day; the whole-stream read keeps such a record with its day. A stream a unit's timeline
and window serve therefore declares the one time field every record of it carries.

## How others cut a log

- **Kafka** cuts a topic's log into segments: the active one is appended to, a closed one is never
  written again, each keeps a time index, and a consumer reads across segments.
- **nautilus_trader** stages its records in Feather files and seals them by size, interval or
  schedule before promoting them into its parquet catalog; a query sees them after the promotion
  ([Stream Data Into a Parquet Catalog](https://nautilustrader.io/docs/latest/how_to/stream_parquet_catalog/),
  read 2026-10-26). A session here also serves the day still being written, because the check-in
  and a viewer read a running session; and it keeps each line as written, where nautilus keeps
  typed columns per data class.
- Kleppmann, *Designing Data-Intensive Applications*, chapter 3, describes the same cut: a log is
  split into segments, a segment is closed when it is full, and records written only partly are
  detected and skipped.
