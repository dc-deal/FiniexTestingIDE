# Safety Circuit Breaker Architecture

## Overview

The Safety Circuit Breaker is an account-level protection mechanism in the AutoTrader tick loop. It monitors balance/equity and drawdown thresholds on every tick and blocks all new position entries when thresholds are breached.

**Key characteristic: AutoTrader pipeline only.** The Safety Circuit Breaker does not exist in backtesting. In simulation, you want to see the full consequences of an algorithm's behavior — including account blowups. The breaker is a *production safety net*, not a simulation constraint.

> **Relation to OrderGuard:** See [Two Independent Safety Layers](order_guard_architecture.md#two-independent-safety-layers) in the OrderGuard doc. OrderGuard = per-order, per-direction. Safety = per-account, session-wide. Independent layers.

---

## How It Works

```
AutoTrader Tick Loop (every tick)
    │
    ├── Compute safety_value:
    │   ├── SPOT:   portfolio.get_spot_equity(mid_price)  (balance + held asset value)
    │   └── MARGIN: executor.get_balance()                (raw balance)
    │
    ├── _check_safety(safety_value, initial_balance)
    │   ├── min threshold check:  SPOT → min_equity, MARGIN → min_balance
    │   ├── max_drawdown check:   (initial - current) / initial > max_drawdown_pct?
    │   └── Sets _safety_blocked = True if EITHER triggers (OR-combined)
    │
    ├── if _safety_blocked:
    │   └── decision overridden to FLAT → send_order() never called
    │       (existing positions run out normally — soft stop, not hard liquidation)
    │
    └── if NOT _safety_blocked:
        └── normal decision execution proceeds
```

### Mode-Specific Evaluation

| Mode | Value checked | Min threshold field | Drawdown basis |
|------|--------------|-------------------|----------------|
| **Spot** | Equity (balance + held asset value at current price) | `min_equity` | `(initial_balance - equity) / initial_balance` |
| **Margin** | Raw balance (changes only on realized P&L) | `min_balance` | `(initial_balance - balance) / initial_balance` |

In spot mode, buying an asset transfers account currency into the asset — the balance drops but portfolio value stays the same. Using equity prevents phantom drawdown triggers from normal trading activity.

### Soft Stop Behavior

When triggered, Safety is a **soft stop**:
- New position entries are blocked (decision forced to FLAT)
- Existing open positions **continue running** — they are NOT force-closed
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
| `min_balance` | float | `0.0` | Block entries if balance drops below this (margin mode). 0 = disabled |
| `min_equity` | float | `0.0` | Block entries if equity drops below this (spot mode). 0 = disabled |
| `max_drawdown_pct` | float | `0.0` | Block entries if session drawdown exceeds this %. Computed from balance (margin) or equity (spot). 0 = disabled |

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
| **AutoTrader** (live/paper) | Yes | Production safety net — prevents account blowup |
| **Backtesting** (simulation) | No | Simulation should show full consequences of algo behavior, including worst-case drawdowns. A breaker would mask problems |

This is a deliberate design choice. Backtesting exists to *find* the scenarios where an algo loses money — artificially cutting losses in simulation defeats the purpose. The operator evaluates drawdown from batch reports and decides whether the algo is safe for live deployment.

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

The detail line shows the active config field name for the current trading model, current value vs threshold, and drawdown headroom. Display data flows through `AutoTraderDisplayStats.safety_blocked`, `safety_reason`, `safety_current_value`, and `safety_drawdown_pct`.

---

## Key Files

| File | Role |
|------|------|
| `python/framework/autotrader/autotrader_tick_loop.py` | `_check_safety()` implementation, equity computation at call site |
| `python/framework/types/autotrader_types/autotrader_config_types.py` | `SafetyConfig` dataclass |
| `python/configuration/autotrader/autotrader_config_loader.py` | Config parsing |
| `python/system/ui/autotrader_live_display.py` | Safety status + detail line rendering |
| `python/framework/trading_env/portfolio_manager.py` | `get_spot_equity()` — equity computation for spot mode |
| `configs/autotrader_profiles/*.json` | Per-symbol safety thresholds |
