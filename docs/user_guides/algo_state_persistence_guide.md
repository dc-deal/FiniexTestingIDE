# Algo State Persistence — Restart-Safe Algo Memory

A long-running live bot loses all in-memory state on every restart (deploy, crash,
container restart, power loss). Over a multi-day session a restart is near-certain. State
persistence lets an algo snapshot and restore **its own internal memory** across restarts —
swing counters, regime flags, "already entered today", risk high-water-marks.

This is **opt-in** and **AutoTrader (live) only**. Backtesting is deterministic and
self-contained — there is no restart concept, so the subsystem never runs there. An algo
that does not opt in is bypassed entirely (no file, no overhead).

## Scope — Category B only

Restart recovery splits into three categories with three owners. This layer is **Category B**:

| Category | Examples | Owner |
|---|---|---|
| A — Broker truth | open positions, resting orders, balances | Broker (Cold-Start Recovery) |
| **B — Algo memory** | counters, regime flags, daily flags, risk HWM | **Bot (this layer)** |
| C — Framework op-state | safety baseline / high-water-mark | Framework (safety layer) |

**Hard boundary:** persist only **position-independent** memory. Do **not** store live-position
references and assume they exist after restart — until broker-side Cold-Start Recovery lands, the
executor's position view starts empty on boot. Persist counters, flags and regime; not "I hold
position #42".

## The hooks

Override these on your `AbstractDecisionLogic` subclass. All have safe no-op defaults.

```python
def uses_state_persistence(self) -> bool:
    return True   # opt in — without this, the whole subsystem is skipped

def get_state_snapshot(self) -> Dict[str, Any]:
    return {'swing_count': self._swing_count, 'entered_today': self._entered_today}

def restore_state(self, snapshot: Dict[str, Any]) -> None:
    self._swing_count = snapshot.get('swing_count', 0)
    self._entered_today = snapshot.get('entered_today', False)

def accepts_restored_state(self, snapshot: Dict[str, Any], ctx: RestoreContext) -> bool:
    # Optional fine-grained freshness gate (see Staleness below). Default: accept.
    return True
```

### JSON-only contract

A snapshot must contain **only JSON primitives** — `str`, `int`, `float`, `bool`, `None`,
`list`, `dict`. Store timestamps as ISO strings. This keeps the file human-readable and the
round-trip symmetric (what you put in is exactly what you get back — no `datetime` silently
returning as a string). A non-serializable value fails the **boot pre-flight** at startup
(`STARTUP FAILED`, naming the offending key). In the backtest the same check runs centrally in
the batch pre-flight — a broken snapshot excludes that scenario with the same message — so you
catch it during development, before going live.

### Empty snapshot = nothing persisted

If `get_state_snapshot()` returns `{}`, no file is written. An algo that opts in but holds no
state yet costs nothing.

## Lifecycle

```
BOOT → warmup → restore (if a valid, fresh snapshot exists) → first decision
RUN  → every save_interval_ticks ticks OR save_interval_seconds seconds → atomic save
STOP → final save → exit
```

Restore runs **after** warmup and **before** the first decision. Saves are atomic (temp file +
rename) — a crash mid-write never corrupts the file. A mid-session save failure is logged but
never aborts the live session.

## Staleness — opening a bot after days away

When you restart a bot whose saved state is older than `max_age_trading_days`, the state is
**discarded** and the bot starts with empty memory. Staleness is **weekend-aware**: trading days
are counted via the market calendar, so a Friday-evening snapshot is not treated as 3 days old on
Monday (Forex). On 24/7 markets (crypto) trading days equal calendar days.

Two policies (config `on_stale`):

- **`warn_reset`** (default) — discard, start fresh, with a prominent warning. The bot resumes
  with reset counters/flags. **Note:** open broker positions are not recognized yet (until
  Cold-Start Recovery) — check your account before letting it run.
- **`halt`** — refuse to boot; you decide (delete the state file to start fresh, or raise
  `max_age_trading_days`). For sharp live bots.

For finer control, override `accepts_restored_state(snapshot, ctx)`: it runs after the coarse
age guard and lets the algo apply its own rule (e.g. a daily flag is stale across a UTC date
boundary even when the coarse guard would keep it). `ctx` carries `saved_at_utc`, `now_utc`,
`age_seconds`, `trading_days`, `weekend_aware` — the framework measures the time so the algo
never reads the wall clock itself.

## Configuration

`app_config.json` → `autotrader.state_persistence` (per-profile override allowed):

```json
"state_persistence": {
    "enabled": true,
    "path": "data/runtime/session_state",
    "save_interval_ticks": 500,
    "save_interval_seconds": 60.0,
    "max_age_trading_days": 5,
    "on_corrupt": "warn_reset",
    "on_stale": "warn_reset"
}
```

State files live at `data/runtime/session_state/<profile>_<symbol>.json` (one per running bot,
stable across runs). Mock adapters auto-disable persistence (a mock session is a dress-rehearsal,
not a real restart context). `on_corrupt` (`warn_reset` / `fail`) governs an unreadable file.

## Worked example

```python
class MyBot(AbstractDecisionLogic):

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._swing_count = 0
        self._last_entry_day = None   # ISO date string

    def uses_state_persistence(self) -> bool:
        return True

    def get_state_snapshot(self) -> Dict[str, Any]:
        return {
            'swing_count': self._swing_count,
            'last_entry_day': self._last_entry_day,
        }

    def restore_state(self, snapshot: Dict[str, Any]) -> None:
        self._swing_count = snapshot.get('swing_count', 0)
        self._last_entry_day = snapshot.get('last_entry_day')

    def accepts_restored_state(self, snapshot, ctx) -> bool:
        # A swing count older than a couple of trading days is no longer meaningful.
        return ctx.trading_days <= 2
```
