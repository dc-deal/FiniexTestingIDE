# Trend Channel Reference — Didactic Strategy Guide

> **Teaching example, not a profitable strategy.** `CORE/trend_channel_reference` exists to
> demonstrate and validate the framework's full order surface end-to-end. It is a deliberately
> mechanical multi-timeframe channel reference — **no profitability is claimed or implied**. Tune
> the defaults freely for exploration; the point is the unified run report and the robustness
> validation tooling, not the P&L.

## What it demonstrates

The three other CORE decision logics (`simple_consensus`, `aggressive_trend`, `cautious_macd`) are
market-order, single-position references. This one is the reference for the **advanced order
surface**:

| Capability | How it is exercised |
|---|---|
| Resting **LIMIT** entry | `limit_pullback` mode — buy a pullback to the lower band (maker fill) |
| Resting **STOP** entry | `stop_breakout` mode — buy a breakout above the band |
| **SL/TP** at submission | every entry carries a stop-loss and take-profit |
| **Trailing stop** (always-on) | the SL ratchets toward price in the profit direction, never back |
| **Partial close** ladder | a fraction of the position is closed at an R-multiple rung |
| **Multi-position** stacking | several concurrent positions on the symbol, each managed independently |
| Resting-order **re-price / cancel** | the resting entry follows the band, and is cancelled if the trend gate flips |
| **Cold-start answer** (#493) | `on_cold_start` — what to do when a restart finds one of its own orders still resting at the venue |

## The strategy (mechanical, textbook)

```
H1 trend gate (CORE/ma_trend)          ── direction screen: longs only if UP, shorts only if DOWN
        │                                 (completed-bar-only, recomputes on H1 close — a stable gate)
        ▼
M15 channel (CORE/bollinger)           ── entry trigger inside the gate's direction
   limit_pullback: LIMIT at the band edge on a pullback   (maker, fill better than market)
   stop_breakout:  STOP beyond the band on a breakout     (momentum continuation)
        │
        ▼
Risk geometry sized off the M15 band half-width (a local volatility unit — no ATR worker needed)
   SL  = entry ∓ sl_mult  · band_half        TP = entry ± tp_mult · band_half
   trailing stop ratchets by trail_mult · (the position's own initial risk)
   partial close: close partial_fraction of the original lots once price reaches partial_rr (R-multiple)
```

**Multi-position note:** the backtest engine runs **one symbol per scenario** today (portfolio
multi-symbol is a later milestone), so "multi-position" means several positions stacked on the
**same** symbol — each with its own SL / TP / trailing stop / partial close.

## Parameters

| Parameter | Default | Meaning |
|---|---|---|
| `entry_mode` | `limit_pullback` | `limit_pullback` (resting LIMIT) or `stop_breakout` (resting STOP) |
| `entry_band_pos` | `0.15` | %B threshold that arms a pullback entry (limit_pullback) |
| `breakout_offset_mult` | `0.25` | STOP trigger distance beyond the band, in band halves |
| `sl_mult` | `1.0` | Stop-loss distance from entry, in band halves |
| `tp_mult` | `2.0` | Take-profit distance from entry, in band halves |
| `trail_mult` | `1.0` | Trailing-stop distance behind price, in R-units of the initial risk |
| `partial_rr` | `1.0` | R-multiple rung at which the partial close fires |
| `partial_fraction` | `0.5` | Fraction of the original lots closed at the partial rung |
| `max_positions` | `2` | Max concurrent positions stacked on the symbol |
| `lot_size` | `0.1` | Fixed entry size |
| `min_free_margin` | `1000` | Margin floor before opening an entry |

The required workers (`CORE/ma_trend` H1, `CORE/bollinger` M15): keep `m15_channel` on the default
`compute_basis: live` (it reads the live band position), and run `h1_trend` with
`"compute_basis": "bar_close"` so the gate is a stable, completed-bar screen (~3× cheaper — it
recomputes once per H1 close). See [Worker Naming](worker_naming_doc.md) for the `compute_basis` axis.

## Running it

The shipped reference sets live in `configs/scenario_sets/backtesting/`:

```bash
# Multi-window IS/OOS robustness demo (limit_pullback, EURUSD + GBPUSD, 36 windows)
python python/cli/strategy_runner_cli.py run backtesting/trend_channel_reference_robustness_test.json

# STOP-breakout validation set (drives the resting STOP path)
python python/cli/strategy_runner_cli.py run backtesting/trend_channel_reference_stop_breakout_test.json
```

The robustness run produces the **ROBUSTNESS VALIDATION** section (distribution + In-Sample /
Out-of-Sample + Walk-Forward Efficiency); see the [Robustness Validation guide](robustness_validation_guide.md).
VS Code: the `🧪 Simulation: Trend Channel Reference (Robustness)` / `(Stop Breakout)` launch entries.

## Parameter sweep

A reference grid sweep ships alongside the bot — a worked example of the parameter-optimization
harness ranking the risk geometry by expectancy:

```bash
# run the grid (sl_mult × tp_mult over a small EURUSD base set), then rank it
python python/cli/optimization_cli.py run trend_channel_reference_grid.json
python python/cli/optimization_cli.py report <sweep_id>   # the run prints the sweep_id
```

- Spec: `configs/sweeps/trend_channel_reference_grid.json` — varies
  `decision_logic_config.sl_mult` × `decision_logic_config.tp_mult`, objective `expectancy`.
- Base set: `configs/scenario_sets/backtesting/trend_channel_reference_sweep_base.json` (two
  EURUSD windows, kept small so the sweep runs quickly).
- VS Code: `✨ Optimization: Trend Channel Reference Grid`.

The harness runs one batch per grid combination, appends each run to the run-results ledger, and
`report` ranks the combinations + shows per-parameter sensitivity. **Reminder:** ranking on a small
fixed window is exactly the curve-fit a single "best" number invites — the robustness validation
above is the honest judge that keeps a sweep result from being self-deception. See
[Parameter Optimization](../architecture/parameter_optimization_system.md).

## What it answers at a restart (`on_cold_start`, #493)

A resting order outlives your process. If the bot is restarted — a deploy, a crash, a
container, the rehearsed weekly restart — the venue is still holding the entry it armed
yesterday, and the boot step rebuilds it into the session's shadow (see
`docs/architecture/live_execution_architecture.md`).

The framework then has a question it cannot answer: **may this session start, given what it
found?** So it asks the strategy. Any logic that declares a resting order type must implement
the hook — a MARKET-only logic is never asked, because it cannot find anything resting:

```python
def on_cold_start(self, situation: ColdStartSituation) -> ColdStartVerdict:
    if situation.is_clean():
        return ColdStartVerdict(True, 'nothing was resting — starting as if fresh')
    ...
    return ColdStartVerdict(
        True,
        f'one armed {self.entry_mode} entry — this logic holds at most one',
        # A yes must NAME every adopted order, or it is not honoured.
        accounted_order_ids=[order.order_id for order in situation.adopted],
    )
```

The full implementation is in `python/framework/decision_logic/core/trend_channel_reference.py`
(`on_cold_start`) — worth reading before writing your own, because it shows the shape of the
answer rather than only its signature.

This reference logic arms at most ONE resting entry at a time, so its answer is simply: one
adopted order of the configured entry type and nothing else → the world is as it left it,
carry on. Two orders, a position that came back without an order, or a book the account no
longer covers → it declines.

One thing this reference deliberately does not do: it only **judges** the situation, it does
not feed it back into its own state. A production bot would — that is what the persisted
memory is for. Here it would mean two subsystems in one teaching example.

Declining is **not** a refusal to start. It hands the decision back to the framework, which
applies its configured policy: `adoption_mode: "auto"` adopts and logs it, `operator_confirm`
refuses and stays flat until somebody looks. The verdict can only ever LOOSEN that policy,
never tighten it — an algo cannot lock itself out of starting.

A production bot would compare the situation against its own persisted memory
(`docs/user_guides/algo_state_persistence_guide.md`) instead of counting orders. This one
deliberately does not: a teaching example that needs two subsystems to be readable teaches
neither. `cautious_macd` shows the other valid shape — a written, reasoned decline, because
the MACD state that set its stop levels is not persisted.

## Reading the strategy's own output

The logic narrates its state per tick (the `notify_awareness` channel — gate direction, armed
setup, no-setup) and emits events on entry / partial / cancel. It also writes a strategy-owned
**setup-funnel** diagnostics CSV (`trend_channel_setups`) into the run's `diagnostics/` folder —
one row per submitted entry (mode, side, gate, entry/SL/TP, band width). See
[Diagnostics CSV Sink](../architecture/diagnostics_csv_sink.md).
