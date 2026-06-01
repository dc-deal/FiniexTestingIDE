# API Monitor Tests Documentation

## Overview

The API monitor test suite validates the broker REST transport-latency monitor
(#351): per-endpoint latency aggregation, error/reject counting, abnormal-only
logging (failures + slow calls), thread-safety, and the config wiring.

**Location:** `tests/autotrader/api_monitor/`

All tests run offline with a `MagicMock` logger (to assert logging) — no network,
no real adapter. The live panel itself is validated by manual observation during a
live run / the Field Study (#332).

---

## Test Structure

```
tests/autotrader/api_monitor/
├── test_api_perf_monitor.py    ← aggregation, error/slow logging, shutdown summary
├── test_thread_safety.py       ← concurrent record() — no lost updates
└── test_api_monitor_config.py  ← loader wiring (mock auto-disable / live default-on)
```

---

## What Each File Validates

| File | Focus |
|------|-------|
| `test_api_perf_monitor.py` | `record()` aggregates count / avg / min / max / last per endpoint; **one row per endpoint** (a repeat call updates, a new endpoint adds); error counting + `last_error` + `total_errors`; **abnormal-only logging** — a failure logs `[API] … failed`, a call over `slow_call_threshold_ms` logs `[API] … slow` + increments `slow_count`, a fast clean call is silent; `shutdown()` emits a per-endpoint summary |
| `test_thread_safety.py` | concurrent `record()` from many threads → no lost updates (count == total); mixed endpoints + errors stay consistent (the monitor is called from the tick-loop thread AND #319/#320/#327 worker threads) |
| `test_api_monitor_config.py` | `load_autotrader_config`: mock auto-disabled (default), live enabled by default, mock + explicit `enabled` overrides the auto-disable, unknown key → `ValueError` |

---

## Key Mechanisms Tested

### One row per endpoint
The monitor keeps one `ApiEndpointStats` per distinct endpoint, updated in place.
The first call to a new endpoint creates a row; every subsequent call to that
endpoint updates it (the live panel therefore stabilizes at ~7 rows — the distinct
Kraken private endpoints — not one row per call).

### Abnormal-only logging
The monitor owns transport performance, so it logs only the abnormal: a failed
call (Kraken `error` response / transport failure) and a call slower than
`slow_call_threshold_ms` (default 3000 ms). Normal fast calls stay silent (no log
flood); their data lives in the panel + the final summary.

### Mock auto-disable
`api_monitor.enabled` defaults to `True` in `app_config.json`, but the loader
auto-disables it for `adapter_type == 'mock'` (a mock has no real `_fetch_private`
transport) — unless the profile sets `enabled` explicitly. Same provenance pattern
as reconciliation / drift_audit.

---

## Fixtures

No shared fixtures — each test constructs an `ApiPerfMonitor` directly with an
`ApiMonitorConfig` and a `MagicMock` logger. Config tests use `tmp_path` profiles
loaded via `load_autotrader_config`.
