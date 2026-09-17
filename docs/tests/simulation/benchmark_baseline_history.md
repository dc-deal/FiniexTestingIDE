# Why the Benchmark Baseline Is What It Is

A throughput baseline that moves without a written reason is worse than no baseline: the next
regression is measured against a number nobody can defend, and a deliberate slow-down becomes
indistinguishable from one that crept in. `reference_systems.json` holds the current numbers and
a short reason; this file holds the evidence behind each change, newest first.

It does NOT describe how to run the benchmark or how to register a new system — that is
[benchmark_tests.md](benchmark_tests.md). Read this one when you want to know why a number is
what it is, or before you change one.

## What a re-registration has to carry

A baseline is re-registered when the code deliberately became slower or faster and the old
number would therefore fail or flatter every future run. Never to make a red run green.

Each entry states, in this order:

1. **Date, `release_version`, `app_version`, and the commit** the new baseline was measured on.
   A baseline measured on a dirty tree describes code nobody can check out; the certificate
   suite refuses one, and so does this record.
2. **Old and new numbers**, with the measurement behind them — the raw runs and their spread.
   A spread near the stability gate means the number is admissible but weakly evidenced; say so.
3. **The cause, and how it was attributed.** "Throughput fell" is not a reason. Which change,
   measured how, and what the control was.
4. **What was recovered before registering.** A baseline that includes an optimisation already
   taken is honest; one registered before the cheap fixes were applied buys a worse number than
   necessary and keeps it for months.
5. **What remains recoverable**, so the next person knows the number is not a floor.

## 2026-09-15 — 90,000 → 63,800 ticks/s

| | |
|---|---|
| `release_version` / `app_version` | 1.4.0 / 1.4.0 |
| Commit | `f7c751c` (clean tree) |
| Certificate | `benchmark_report_1.4.0_2026-09-15_203033.json` |
| Previous baseline | registered 2026-04-27, last green run 2026-08-26 |

### The numbers

| Metric | Old baseline | New baseline | Last measurement (26.08) | Certificate (15.09) |
|---|---|---|---|---|
| `ticks_per_second` | 90,000 | **63,800** | 85,230 | 61,925 |
| `tickrun_time_s` | 17.0 | **23.4** | 17.56 | 24.16 |
| `warmup_time_s` | 6.5 | **6.6** | 6.70 | 6.73 |

Measured-to-measured the loss is **−27.3 %** of throughput, or **+48.9 %** of per-tick work.

The new baseline is the median of three runs on an idle machine with warm caches — raw
64,706.84 / 63,826.81 / 61,540.70, spread **5.1 %**. A first sitting the same hour produced a
12.6 % spread whose lowest run was a clear cold-start outlier; it was discarded and re-measured
rather than registered, because this number stands for months. The certificate run that followed
came in at a 3.2 % spread, the tightest this instrument has produced.

**The control:** `warmup_time_s` is unchanged (6.70 → 6.73 s). Warmup is IO-bound. A throttled
machine, a slower disk or a busier host would have moved it too, and it did not — so the cause is
in the code, not in the environment.

### Where it went

Measured with the benchmark's own per-operation instrumentation, milliseconds per tick:

| Operation | 26.08 | 15.09 | Δ | Share of the increase |
|---|---|---|---|---|
| `worker_decision` | 0.1994 | 0.2779 | +0.0785 | 65 % |
| `equity_sample` | — | 0.0361 | +0.0361 | 30 % |
| `trade_simulator` | 0.0129 | 0.0158 | +0.0029 | 2 % |
| `bar_rendering` | 0.0294 | 0.0324 | +0.0030 | 2 % |
| `live_update` | 0.0037 | 0.0047 | +0.0010 | 1 % |
| **per tick** | **0.2483** | **0.3698** | **+0.1215** | **+48.9 %** |

### How it was attributed

Fifty-one commits lie between the two measurements, so the obvious method was a bisect. A
profile comparison was chosen instead because it names the FUNCTION rather than the commit, and
because it needs no clean tree: the same one-scenario workload was profiled at four commits, each
in its own `git worktree` with the real data archive symlinked in, so the main tree was never
checked out. Every run processed **exactly 49,196 worker passes** — that identity is what makes
the four comparable.

| Commit | | Worker pass (cumulative) |
|---|---|---|
| `b985a2d` | 26.08, the last green benchmark | 5.72 s |
| `1185e9a` | 14.09, before the indicator centralisation | **5.08 s** |
| `b7f1a57` | 15.09, after it, plus the per-tick equity sample | **7.83 s** |
| `4a35f06` | later the same day | 7.26 s |

**The regression is not spread across fifty-one commits — it is one step.** The forty-nine
commits before the indicator work made the worker path slightly faster.

Two changes account for it, and both were chosen knowingly:

**The indicator library (+1.54 s of worker self-time).** Wilder's smoothing is the standard
definition of the RSI and of the ATR, and it is recursive: over exactly `period` values it
returns its own seed, so it needs five periods of history. An RSI(14) therefore reads 71 bars
where the old inline version read 15, and smooths them instead of averaging them. What ran under
the name "RSI" before was Cutler's variant, and what ran under "ATR" was an EMA — an ATR(14) that
behaved like a Wilder ATR(7.5). The cost is not a mistake; it is the price of the names being
true.

**The per-tick equity sample (+0.73 s over its whole call chain).** The drawdown is now measured
on every tick in both pipelines rather than on closed trades, which is the measure the word means
outside this repository and the one the thirty-day parity run needs. Of that cost, `_accrue_swap`
is 0.32 s — recomputing the swap rollover window on every tick for a window of milliseconds,
when a rollover happens at most once a day. **That part is waste rather than correctness** and is
recorded below.

### What was recovered before registering

The recursion behind both exponential averages was rewritten in closed form
(`recursive_average.py`) — one dot product against a geometric weight vector instead of a Python
loop over the window. Verified equal to the recursion over 378 cases spanning periods 2-200,
window lengths from exactly-the-period to 1000, and price scales from 0.0001 to 88,000; the worst
disagreement is 5.1e-15 relative, about 23 float64 ULPs.

Worth **5.0 %** of tick-run time, measured by interleaved wall-clock A/B with no overlap between
the groups (loop 4.4509 / 4.3984 / 4.3952 s against numpy 4.2933 / 4.1033 / 4.1781 s). **This
baseline is measured with it.**

A note for whoever measures the next one: **cProfile cannot value a change like this and gets its
sign wrong.** It charges per function call, so a loop's iterations are invisible to it while the
numpy calls replacing them are counted — the profile reported the vectorised version as 4 %
*slower* while the timed A/B showed it 5 % faster. Use a profile to locate, a timed A/B to value.

### What is still recoverable

- **The swap recomputation.** `_accrue_swap` runs on every tick per open position and rebuilds
  the rollover window each time — 22,617 calls with 45,234 timezone resolutions in one scenario.
  Remembering the next rollover instant per position would remove nearly all of it, worth roughly
  4 % of throughput. It sits in the fee path, so it needs care rather than speed.
- **The compute basis.** Both workers in this scenario run on `ComputeBasis.LIVE` and recompute
  on every tick — 49,196 times, while the M5 bar closes 144 times and the M30 bar 24 times in the
  same window. That is deliberate (the forming bar counts), but it is why a five-fold window
  reaches the throughput at all: the cost of the correct definition is multiplied by the tick
  rate rather than the bar rate. Changing it would change what a strategy sees, and the benchmark
  workload is frozen by its own `config_contract`, so this is a question for a real bot rather
  than a lever for this number.
