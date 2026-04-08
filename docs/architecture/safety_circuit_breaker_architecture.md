# Safety Circuit Breaker Architecture

## Overview

The Safety Circuit Breaker is an account-level protection mechanism in the AutoTrader tick loop. It monitors balance and drawdown thresholds on every tick and blocks all new position entries when thresholds are breached.

**Key characteristic: AutoTrader pipeline only.** The Safety Circuit Breaker does not exist in backtesting. In simulation, you want to see the full consequences of an algorithm's behavior — including account blowups. The breaker is a *production safety net*, not a simulation constraint.

> **Relation to OrderGuard:** See [Two Independent Safety Layers](order_guard_architecture.md#two-independent-safety-layers) in the OrderGuard doc. OrderGuard = per-order, per-direction. Safety = per-account, session-wide. Independent layers.

---

## How It Works

```
AutoTrader Tick Loop (every tick)
    │
    ├── _check_safety(balance, initial_balance)
    │   ├── min_balance check:    balance < min_balance?
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

### Soft Stop Behavior

When triggered, Safety is a **soft stop**:
- New position entries are blocked (decision forced to FLAT)
- Existing open positions **continue running** — they are NOT force-closed
- SL/TP triggers still execute normally
- If balance recovers above thresholds (e.g. open position becomes profitable), the breaker **automatically clears** and trading resumes

This is intentional — a hard liquidation during a temporary drawdown could lock in losses that would have recovered.

---

## Configuration

Configured in AutoTrader profile JSON (`configs/autotrader_profiles/*.json`):

```json
"safety": {
    "enabled": false,
    "min_balance": 5.0,
    "max_drawdown_pct": 30.0
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `enabled` | bool | `false` | Master switch — must be explicitly enabled |
| `min_balance` | float | `0.0` | Block entries if balance drops below this (account currency). 0 = disabled |
| `max_drawdown_pct` | float | `0.0` | Block entries if session drawdown exceeds this %. 0 = disabled |

Both conditions are OR-combined — either alone triggers the block.

### SafetyConfig Dataclass

```python
@dataclass
class SafetyConfig:
    enabled: bool = False
    min_balance: float = 0.0
    max_drawdown_pct: float = 0.0
```

Located in `python/framework/types/autotrader_types/autotrader_config_types.py`.

---

## Known Issue: Spot Mode Phantom Drawdown (#270)

In spot mode, `_check_safety` uses raw account currency balance. When buying an asset (e.g. ETH), USD transfers into ETH — the USD balance drops but portfolio value stays the same. Safety interprets this as a loss.

```
Initial:    12.49 USD, 0 ETH
Buy 0.001 ETH @ $2,141 → cost ~$2.15
After Buy:  10.34 USD, 0.001 ETH

Safety sees: drawdown = (12.49 - 10.34) / 12.49 = 17.2%
Reality:     portfolio value ≈ $12.48, actual drawdown ≈ 0%
```

**Fix (planned in #270):** Use equity (balance + unrealized asset value) instead of raw balance in spot mode. `PortfolioManager.get_account_info()` already computes equity — the fix is routing it to `_check_safety`.

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
Safety:  ● ACTIVE          (enabled, not triggered)
Safety:  ⛔ BLOCKED         (triggered — entries blocked)
Safety:  off               (disabled in config)
```

Display data flows through `AutoTraderDisplayStats.safety_blocked` and `safety_reason`.

---

## Key Files

| File | Role |
|------|------|
| `python/framework/autotrader/autotrader_tick_loop.py` | `_check_safety()` implementation (lines 370-408) |
| `python/framework/types/autotrader_types/autotrader_config_types.py` | `SafetyConfig` dataclass |
| `python/configuration/autotrader/autotrader_config_loader.py` | Config parsing |
| `python/system/ui/autotrader_live_display.py` | Safety status rendering |
| `configs/autotrader_profiles/*.json` | Per-symbol safety thresholds |
