# Safety Circuit Breaker Architecture

> **The denominator this measures against is a premise, not a fact (#489).** Every threshold
> below compares against the ACCOUNT — its balance, its equity, its drawdown. That is the bot's
> own denominator only if the account is only the bot's, and a framework cannot read that off a
> venue. The operator declares it with `capital.exclusive_account`, and the boot then checks it:
> a stranger's order on the bot's own instrument refuses the start, one elsewhere is a Tier-1
> warning. Left at its default (`false`), the thresholds here still work — they simply measure a
> denominator that may include capital this bot does not own. See
> [autotrader_capital_and_safety.md](../autotrader/autotrader_capital_and_safety.md) — *Whose account is it*.

## Overview

The Safety Circuit Breaker is an account-level protection mechanism of the AutoTrader session (`SafetyCircuitBreaker`, which the tick loop feeds on every tick). It monitors the account value and the drawdown on every tick, and while a threshold is breached it forces every decision's action to FLAT — no new entry, and no close or reduction a strategy expresses as a BUY or SELL either.

**Key characteristic: AutoTrader pipeline only.** The Safety Circuit Breaker does not exist in
backtesting. In simulation, you want to see the full consequences of an algorithm's behavior —
including account blowups. The breaker is a *production safety net*, not a simulation constraint.

> **Relation to OrderGuard:** See [Two Independent Safety Layers](order_guard_architecture.md#two-independent-safety-layers) in the OrderGuard doc. OrderGuard = per-order, per-direction. Safety = per-account, session-wide. Independent layers.

---

## How It Works

```
AutoTrader Tick Loop (every tick)
    │
    ├── Compute safety_value:
    │   └── portfolio.get_account_value()    ONE definition, per account model:
    │       ├── SPOT:   balance + held assets at the current mid price
    │       └── MARGIN: balance + unrealized P&L
    │
    ├── SafetyCircuitBreaker.check_safety(safety_value, baseline)
    │   ├── min threshold check:  SPOT → min_equity, MARGIN → min_balance
    │   ├── drawdown checks:      pct and abs, against the risk baseline
    │   ├── daily loss checks:    pct and abs, against the DAY's baseline
    │   └── Sets _safety_blocked = True if ANY triggers (OR-combined, every one named)
    │
    ├── if _safety_blocked:
    │   └── decision's action overridden to FLAT → no order from the action
    │       (an action-driven close is suppressed too; position management, resting
    │        orders and SL/TP keep working — soft stop, not hard liquidation)
    │
    └── if NOT _safety_blocked:
        └── normal decision execution proceeds
```

### Mode-Specific Evaluation

| Mode | Value checked | Min threshold field | Drawdown basis |
|------|--------------|-------------------|----------------|
| **Spot** | Account value: balance + held assets at the current price | `min_equity` | `(baseline - value) / baseline` |
| **Margin** | Account value: balance + unrealized P&L | `min_balance` | `(baseline - value) / baseline` |

In spot mode, buying an asset transfers account currency into the asset — the balance drops but portfolio value stays the same. Using the account value prevents phantom drawdown triggers from normal trading activity.

**Both rows read the SAME function since #356**, and only the threshold FIELD differs by model —
the name says which config key a profile writes, never which quantity is compared. Margin used to
read settled cash, which by construction moves only on realised P&L: the account model that can
lose more than it holds was the one whose breaker could not see an open loss coming, while the
drawdown series three methods away had the right number all along.

### Soft Stop Behavior

When triggered, Safety is a **soft stop**:
- The decision's action is forced to FLAT: no new entry, and no close or reduction the strategy
  expresses as a BUY or SELL — a strategy that reduces by deciding SELL keeps its exposure for as
  long as the block holds
- What a strategy sends from its own position management whatever the action — a close, a stop
  amend, a cancel — still goes out, and a resting order can still fill
- Existing open positions are NOT force-closed by the breaker
- SL/TP triggers still execute normally
- If the checked value recovers above thresholds (e.g. equity rises), the breaker **automatically clears** and trading resumes

This is intentional — a hard liquidation during a temporary drawdown could lock in losses that would have recovered.

---

## Configuration

Configured in AutoTrader profile JSON (`configs/autotrader_profiles/*.json`):

```json
"safety": {
    "enabled": true,
    "min_balance": 500.0,
    "min_equity": 5.0,
    "max_drawdown_pct": 30.0
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `false` | Master switch — must be explicitly enabled |
| `min_balance` | float | `0.0` | Force decisions FLAT while the account value is below this — the key a margin profile writes. 0 = disabled |
| `min_equity` | float | `0.0` | The same floor, the key a spot profile writes. 0 = disabled |
| `max_drawdown_pct` | float | `0.0` | Force decisions FLAT while the session drawdown exceeds this %, on the account value in both models (#356). 0 = disabled |

Both conditions are OR-combined — either alone triggers the block. Each mode uses its own min-threshold field; the other is ignored.

### SafetyConfig Dataclass

```python
@dataclass
class SafetyConfig:
    enabled: bool = False
    min_balance: float = 0.0
    min_equity: float = 0.0
    max_drawdown_pct: float = 0.0
```

Located in `python/framework/types/autotrader_types/autotrader_config_types.py`.

---

## Pipeline Availability

| Pipeline | Safety Available | Rationale |
|----------|-----------------|-----------|
| **AutoTrader** (every session: mock, dry run, real orders) | Yes | Production safety net — prevents account blowup |
| **Backtesting** (simulation) | No | Simulation should show full consequences of algo behavior, including worst-case drawdowns. A breaker would mask problems |

This is a deliberate design choice. Backtesting exists to *find* the scenarios where an algo loses
money — artificially cutting losses in simulation defeats the purpose. The operator evaluates
drawdown from batch reports and decides whether the algo is safe to trade real money.

---

## Live Display

The AutoTrader live monitor shows safety state in the SESSION panel:

```
Safety:  ● ACTIVE  (spot)
         min_equity: 5.00 (now: 12.48)  |  dd: 0.1% / 30.0%
```

When blocked:
```
Safety:  ⛔ BLOCKED  min_equity (4.80 < 5.00)
         min_equity: 5.00 (now: 4.80)  |  dd: 35.1% / 30.0%
```

Margin equivalent:
```
Safety:  ● ACTIVE  (margin)
         min_balance: 500.00 (now: 9823.45)  |  dd: 1.8% / 30.0%
```

Disabled thresholds show `off`:
```
Safety:  ● ACTIVE  (spot)
         min_equity: off  |  dd: 0.1% / 30.0%
```

When safety is disabled:
```
Safety:  off
```

The detail line shows the active config field name for the current trading model, current value vs
threshold, and drawdown headroom. Display data flows through
`AutoTraderDisplayStats.safety_blocked`, `safety_reason`, `safety_current_value`, and
`safety_drawdown_pct`.

---

## The End-of-Session Report

Every drawdown figure the session produces names the baseline it was measured against. That is
not decoration: four quantities in this codebase are called "initial", none of the older ones
recorded when or at what price it was taken, and two of them are different numbers for the same
holdings — so a bare "−12 %" could not be traced back to the denominator that produced it.

The report is written to `io/safety.json` and rendered as a block in the AutoTrader session
summary. It goes through the unified reporting pipeline (`docs/architecture/reporting_pipeline.md`):
the safety circuit breaker CAPTURES, `safety_report_builder.py` DERIVES, the console only formats.

### What it answers, and why each answer has the shape it has

**The denominator.** The `RiskBaseline` record travels whole — its kind, its stamp, its origin,
and on a spot account the price and the holdings its value can be re-derived from. A record that
came back from the previous session is flagged separately, because that is the whole point of the
persistence and one boolean away from invisible.

**And it is not the only honest drawdown the session produces.** The portfolio section reports a
SECOND one, and the two may legitimately differ — so each says which it is rather than printing a
bare percentage. The safety reading is measured against this configured baseline (`fixed` or
`high_water_mark`); the portfolio's `Max DD (account, curve)` is the largest peak-to-trough
decline of the equity curve, which is what the word means outside this project and what a sweep
ranks on. On a `fixed` baseline that never rose they agree; on a `high_water_mark` they are the
same construction; after a recovery they are different numbers about the same session. Merging
them is the defect #497 removed, and the labels are what keep them apart.
`safety.persist_baseline` governs BOTH across a restart — deliberately one switch, because a
session where only one survived would show two figures silently describing different periods.

**How far the account actually moved.** The extremes are RUNNING MAXIMA, not the value at the end.
A session that touched 18 % at hour three and recovered by hour four ends at −0 %, and read from a
session-end snapshot it is indistinguishable from one that never moved. Over thirty unattended days
that is the difference the operator's next decision hangs on.

**Two extremes, because a high-water mark moves.** 1 000 below a baseline of 10 000 is 10 %; 1 200
below a later peak of 20 000 is the larger amount and the smaller share. Tracking only one of them
would report 6 % as the session's worst percentage while the percentage limit was breached at 10 %.
With the default fixed baseline the two are the same instant and agree by construction — the second
line only appears when they genuinely differ.

**How close it came.** One figure per threshold: the share of the configured limit the worst
excursion consumed. The percentage limit and the absolute one are independent and either can fire
first, so the figure reports whichever came CLOSER. Where no limit is configured it is `null`, never
zero — "no limit" and "nothing used of the limit" are different statements.

**One row per trading day.** A daily limit is measured against a reference struck fresh every morning,
so a single "worst daily loss" across a month would be a maximum across thirty different
denominators. Each row names its own day-start baseline. The console prints the deepest day plus
every day that tripped a limit; the artifact carries them all.

**What the hard stop did.** Whether it fired, what tripped it, and how it ended —
`flatten_outcome`: `complete` (the closes landed, the book is flat), `holdings_kept` (spot holdings
kept on purpose because `spot_liquidate_to_quote` is off, named in `flatten_kept`) or `unconfirmed`
(closes sent and not confirmed, named in `flatten_unconfirmed`). The middle one is kept apart from
the last on purpose: both leave something open at the venue, but only an unconfirmed close asks the
operator to check the account, and the same words beside a kept holding invite the sale that
realises the loss the switch keeps unrealised. `flatten_outcome` is `null` when the stop never
fired, which a reader must be able to tell apart from every outcome — a drain still running when the
session ended for another reason is resolved to `unconfirmed` at capture, with the positions still
open at the venue named.

### It is written even when the limits are OFF

The record is produced whenever a baseline was taken, including for a session with
`safety.enabled: false`. That session still has a denominator and still moves against it, and the
resulting record is what says what WOULD have fired. Refusing to measure it would mean arming a real
limit in order to find out what the limit should be.

Absent only when no baseline was ever taken — a session that saw no tick it could value.

---

## Key Files

| File | Role |
|------|------|
| `python/framework/autotrader/safety/safety_circuit_breaker.py` | The breaker: soft block, hard stop, daily loss, and the record for the report |
| `python/framework/autotrader/autotrader_tick_loop.py` | Feeds it: the account value per tick, the entry block before each decision |
| `python/framework/types/autotrader_types/autotrader_config_types.py` | `SafetyConfig` dataclass |
| `python/configuration/autotrader/autotrader_config_loader.py` | Config parsing |
| `python/system/ui/autotrader_live_display.py` | Safety status + detail line rendering |
| `python/framework/trading_env/portfolio_manager.py` | `get_spot_equity()` — equity computation for spot mode |
| `configs/autotrader_profiles/*.json` | Per-symbol safety thresholds |
| `python/framework/autotrader/safety/risk_baseline_tracker.py` | `RiskBaselineTracker` — takes, restores and advances the denominator |
| `python/framework/types/autotrader_types/safety_session_types.py` | What the breaker captures for the report |
| `python/framework/reporting/builders/safety_report_builder.py` | DERIVE — the `SafetyReport` model |
| `python/framework/reporting/console/autotrader_session_summary.py` | PRESENT — the closing-block safety section |
