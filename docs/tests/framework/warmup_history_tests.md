# Warmup History Tests

`tests/framework/warmup_history/` — the bars a run starts from, pinned across every path that
builds them (#588): a backtest, a mock AutoTrader session and a live-adapter session. The rule and
the components are described in [Warmup History](../../architecture/warmup_history.md).

## Why this suite exists at all

The warmup history had been wrong twelve times since 2025-09, and no test had found a single one:
each path was checked against itself, never against the others. The backtest selected bars by
their opening stamp and so warmed up on the bar containing its start, the live fetch dropped a row
by its position, and the parity guard on the mock profile compared opening stamps too — it let
through a bar that ended 219 seconds after the start. This suite feeds ONE synthetic tick stream to
all paths and compares what they hold at the first tick.

Only the venue's transport is replaced. The archive is rendered by the importer's own
`VectorizedBarRenderer` and read through the batch's own `SharedDataPreparator.prepare_bars`; the
live path runs the real `KrakenOhlcBarFetcher`, `AutotraderWarmupPreparator`, `WarmupHistory` and
bar controller, with `_fetch_ohlc` answering from the same stream the way Kraken does at a given
instant — one open-stamped row per period, the last one still forming, and a `last` field naming
the newest committed period (measured 2026-10-12). The builders live in
`warmup_history_builders.py`; `conftest.py` holds the two-day stream and the preparator over its
archive.

Every test in `test_warmup_across_paths.py`, `test_warmup_selection.py` and
`test_start_invariance.py` fails on the selection the backtest used before #588 — measured by
putting the old rule back: ten tests red, among them 1,296 of 1,296 RSI values differing between
two starts.

## What Is Tested

### `test_bar_period.py`

The module itself: the grid every timeframe shares, the boundary to the second, and the series
form (`complete_at_mask`) held to the scalar one.

| Class | What it pins |
|-------|-------------|
| `TestTheGrid` | every registry timeframe divides a UTC day; a period contains its instant and opens on the grid; an instant on a boundary opens the new period; a day opens at midnight UTC |
| `TestCompleteAt` | complete exactly at the period's end and not a second earlier; the bar containing an instant is never complete at it; an opening before the start does not make a bar complete; the series form agrees with the scalar form |
| `TestSelection` | the newest N complete bars are taken; fewer than asked returns what there is; a bar's ISO stamp reads back as its opening instant |

### `test_warmup_across_paths.py` — the core

The same start through the backtest, the mock session and the live session; after the first tick
every timeframe (M1, M5, H1, D1) must hold the same bars in all three, each complete at that tick,
and the first rendered bar must follow the history without repeating it.

| Test | Description |
|------|-------------|
| `test_a_start_inside_a_bar` | 13:02:30 lies inside an M5, an H1 and a D1 bar; none is taken, and nothing is read again |
| `test_a_period_ending_between_the_read_and_the_first_tick` | read at 13:04:58, first trade at 13:05:04 — exactly M1 and M5 are read again |
| `test_midnight_between_the_read_and_the_first_tick` | read at 23:59:59, first trade at 00:00:02 — the day that just ended is in the history |
| `test_the_histories_stay_one_after_the_first_rendered_bar_closes` | ten minutes on, the bars each path rendered itself have joined the history alike |
| `test_a_period_the_venue_has_not_committed_yet` | a first trade inside the venue's commit lag — the session refuses instead of trading on a hole |
| `test_a_venue_that_cannot_be_read_again` | the re-read fails — refused, before the first decision |

### `test_venue_warmup_read.py`

The live fetch takes a row by what it is, never by where it stands: answers in which position and
content disagree.

| Test | Description |
|------|-------------|
| `test_the_forming_row_is_dropped` | the ordinary answer |
| `test_a_complete_last_row_is_kept` | an answer without a forming row — dropping the last row cost a complete bar |
| `test_a_row_the_venue_has_not_committed_is_dropped` | our clock is past the period's end, the venue still adds to it |
| `test_an_uncommitted_row_is_dropped_wherever_it_stands` | the uncommitted period sits before a fresh row |
| `test_a_row_not_complete_at_the_reference_is_dropped_even_if_committed` | our clock is behind the venue's |
| `test_without_a_commit_marker_the_period_rule_alone_decides` | an answer without `last` |
| `test_only_the_newest_count_bars_are_returned` | the tail |
| `test_the_read_reaches_back_from_the_reference_not_from_the_wall_clock` | `since` is counted from the instant asked about |
| `test_the_fetched_bars_claim_no_completeness_of_their_own` | `is_complete` is stamped by the injection guard after it has checked |
| `test_the_measured_commit_lag_holds_the_period_back` | 0.3, 0.79 and 1.5 s after a boundary — parametrized, from the two measurements |

### `test_recorded_kraken_answer.py`

Two answers Kraken really sent, verbatim, either side of the moment it committed a just-ended
minute (`tests/fixtures/warmup_history/kraken_ohlc_answers.json`, recorded 2026-10-13 by
`python/experiments/venue_probes/probe_kraken_ohlc_commit.py`), each read by the real fetcher at
the instant it arrived. `VenueStub` answers in the shape we measured; this pins the shape the venue
sends — its pair key is `XETHZUSD`, not the `ETHUSD` asked for.

| Class | What it pins |
|-------|-------------|
| `TestTheRecordedShape` | one pair key beside `last`; rows of eight fields stamped with their opening minute, prices as strings; the newest row after the commit marker |
| `TestTheReaderOverTheRecording` | before the commit the minute that just ended is not taken although its time is up; after it, it is taken and the new minute is not; the prices arrive as numbers |

The recording's freshness is held by the live adapter release gate
(`tests/live_adapters/test_kraken_ohlc_answer_shape.py`), which asks the venue at release time
whether it still answers in this shape.

### `test_warmup_selection.py`

The batch's own selection over the synthetic archive.

| Test | Description |
|------|-------------|
| `test_a_start_inside_a_bar` | every selected bar is complete at the first tick; the bar holding it is not selected |
| `test_a_start_on_a_boundary_takes_the_bar_that_just_ended` | three seconds after 13:00, the 12:00 bar is the newest |
| `test_a_period_that_ends_before_the_first_tick_is_in_the_history` | a stream silent from 12:59:40 to 13:00:20 and a start at 12:59:50 — selected for the configured start, the 12:00 bar would be missing |
| `test_two_scenarios_sharing_a_window_keep_their_own_warmup` | same symbol, timeframe and start, different requirements — the old key let one overwrite the other |
| `test_a_scenario_without_a_first_tick_is_selected_at_its_start` | the fallback, so the selection still says what it used |

### `test_start_invariance.py`

The idea behind Freqtrade's lookahead and recursive analysis: the CORE RSI on D1, started at
midnight and at 13:00 the same day, must compute the same value on every tick from the next day on
— a close-based indicator reads a day's close, and the close does not depend on where the run
started. Thirty days of synthetic trades; the comparison spans more than a thousand ticks.

### `test_warmup_guard.py`

The one route into the renderer's history and the renderer's own check at its first bar.

| Class | What it pins |
|-------|-------------|
| `TestTheInjectionRoute` | a bar not complete at the reference, a repeated or reordered period, and a bar of another timeframe are refused; an accepted history is stamped complete; injecting again replaces the history (the seam's re-read) |
| `TestTheFirstRenderedBar` | a history reaching into the first rendered bar is refused; one ending where it opens is accepted |
| `TestTheSeamHook` | the seam closes once, with the first tick, before anything is rendered |

### `test_warmup_record.py`

A session writes down the history it started from, `io/warmup_history.json`, at injection and again
once the seam has closed.

| Test | Description |
|------|-------------|
| `test_a_mock_session_records_its_history_and_its_first_tick` | source, the instant selected for, every bar as the controller holds it; the first tick added once it arrives |
| `test_a_live_session_records_the_history_after_the_seam` | the boot read, then the re-read timeframes and the bars after the seam |
| `test_without_a_path_nothing_is_written` | no path, no file |

### `test_first_tick_refusal.py`

A session that cannot close its warmup seam says it refused, not that it crashed — console, session
log and result — while its grading stays `FAILED` (exit 2), so whoever relaunches sessions
relaunches it.

| Test | Description |
|------|-------------|
| `test_a_seam_refusal_in_the_tick_loop_is_a_refusal` | the `⛔ REFUSED AT THE FIRST TICK` block, not `RUNTIME ERROR` |
| `test_the_refusal_reaches_the_session_log` | one error line in the session log, nothing in the global log |
| `test_a_crash_in_the_tick_loop_still_reads_as_one` | any other exception keeps `RUNTIME ERROR — SESSION ABORTED` |
| `test_the_same_refusal_before_the_tick_loop_is_a_startup_failure` | the boot refusals keep `STARTUP FAILED` |
| `test_a_refused_session_is_still_failed_and_exits_2` | the grading is unchanged |
| `test_the_result_carries_the_refusal` | the flag reaches `AutoTraderResult`, where the summary reads it |

### `test_warmup_outcomes.py`

The outcome table: one shortfall rule, and what each pipeline does with it.

| Class | What it pins |
|-------|-------------|
| `TestTheBacktestOutcomes` | an empty or missing timeframe refuses the scenario (`warmup_history_empty`); a short history warns (`warmup_quality`) and runs; a requirement beyond `bar_max_history` refuses it (`warmup_capacity`) |
| `TestTheSessionOutcomes` | capacity refused in both adapter paths; an empty timeframe refused in a mock and in a live session; an unreadable venue refused; venue bars without a trade named in a warning, archive bars never; a short history warns and starts in both paths |

The agreement between a worker's two warmup answers — refused when the worker is created — is
held by `tests/framework/worker_tests/test_warmup_requirement_agreement.py`
([Worker Tests](worker_tests.md)), beside the per-worker cases it grew from.

## Running

```bash
pytest tests/framework/warmup_history/ -v
```

Runs in a few seconds; it reads no archive data and no network. VS Code: `🧩 Pytest: Warmup History (All)`.
