# Swap / Overnight-Funding Cost Model

How FiniexTestingIDE charges **swap** (overnight financing) in the backtest, why it is
signed, when it triples, and how the time of the charge is derived deterministically.
Part of the Trading-Realism (TRO) cluster — correctness, not cosmetic realism.

> Scope: simulation (backtest) accrual on **MARGIN/Forex** markets. Spot markets (crypto)
> have no swap. Live broker-reported swap reconciliation arrives with the MT5 adapter.

---

## What swap is

Holding a leveraged position overnight means financing it: you effectively borrow one
currency to hold another, and the **interest-rate differential** between them is settled
daily at the broker's rollover. Swap is therefore **not a commission** — it is recurring
financing, and it can go **either way**:

- **Debit** (you pay) — the currency you are effectively borrowing carries the higher rate.
- **Credit** (you receive) — the currency you hold carries the higher rate (*positive carry*).

Swap is **independent of price direction** — it depends on the rate differential, not on
whether the trade wins or loses. A position bleeds (or earns) swap every night it is held.

### Sign convention

Swap is stored **signed**, consistent with the portfolio's `net = gross − total_fees`:

| Case | broker rate | stored cost | P&L effect |
|---|---|---|---|
| Debit (you pay) | e.g. `swap_long = −7.85` | **positive** cost | reduces P&L |
| Credit (you receive) | e.g. `swap_short = +3.80` | **negative** cost | raises P&L |

`cost = −(swap_rate_points × days × tick_value × lots)`. In reports, `total_swap` is signed —
a **negative** `total_swap` means net swap was *earned*.

### Why broker rates are asymmetric

A broker's `swap_long` and `swap_short` are rarely symmetric (e.g. USDJPY `+9.17 / −18.95`)
because each carries a **markup against the trader on both sides** on top of the true
differential. Decompose: `true_diff = (long − short)/2`, `markup = −(long + short)/2`. The
markup is the broker's guaranteed, direction-independent revenue on every overnight hold.

---

## When swap is charged — the rollover

Swap books at the broker's **daily rollover** — the FX-market convention is **17:00
New York** (end of the NY trading day / new value date). A position is charged **once per
rollover it is held across** — it is a **step at the boundary, not a per-minute proration**:

- Open *and* close within one rollover interval (no boundary crossed) → **0 swap**, however long.
- Held across one boundary → **one full night** (or triple), whether 2 minutes or 23 hours.

So intraday strategies pay no swap; swing/position strategies pay (or earn) it every night.

### Triple swap (T+2 settlement)

Once a week — on the broker's configured weekday (`swap_rollover3days`, usually **Wednesday**)
— the rollover books **3 days** instead of 1. FX settles **T+2**: a position over Wednesday's
rollover has its value date jump across the weekend (Fri+Sat+Sun), so the weekend's financing
is booked in advance on Wednesday. Mon–Fri rollovers with the Wednesday triple sum to the
**7 calendar days** of weekly financing. Saturday/Sunday carry no rollover (market closed).

### DST — the rollover time is local

`17:00 America/New_York` maps to a **different UTC instant** by season:

```
Winter (EST, UTC−5):   17:00 NY  =  22:00 UTC
Summer (EDT, UTC−4):   17:00 NY  =  21:00 UTC
```

The model stores the rollover anchor as **local time + IANA timezone** and resolves it
per date via the stdlib `zoneinfo` (DST-aware). A fixed UTC hour would be one hour off for
half the year. (`tzdata` is pinned in `requirements.txt` so the IANA database is present in
slim/Docker environments.)

### Worked examples (EURUSD, USD account, 1 lot, tick_value 1.0; winter rollover 22:00 UTC)

```
A — LONG, swap_long = −7.85 (debit)
    Open Mon 10:00 → close Wed 09:00
    crosses Mon 22:00 (×1) + Tue 22:00 (×1)          → +15.70 USD cost (debit)

B — SHORT, swap_short = +3.80 (credit), over the Wednesday triple
    Open Tue 12:00 → close Thu 08:00
    crosses Tue 22:00 (×1) + Wed 22:00 (×3 triple)   → −15.20 USD cost (credit, earned)
```

---

## Margin coupling

Swap is **not** part of the margin *requirement* (that is size-based). But accrued swap
flows into **equity** (`equity = balance + Σ unrealized_pnl`, and `unrealized_pnl` includes
swap), so it lowers **free margin** and the **margin level** — a position bleeding swap moves
closer to a stop-out (interacts with simulated liquidation, #366).

---

## Architecture

```
MarketConfigManager.get_swap_rollover(broker_type)   → rollover anchor (forex) / None (crypto)
        │ (resolved ONCE per session)
        ▼
MarketClock (executor-owned)  ── canonical clock + MarketCalendar + rollover config
        ├── feeds PortfolioManager accrual (the rollover anchor)
        └── answers DecisionTradingApi awareness queries (the API only forwards)
```

- **Accrual** (`PortfolioManager._accrue_swap`): on every position refresh, for each open
  position it asks `MarketCalendar.iter_swap_rollovers(swap_accrued_until, now, …)` for the
  rollover crossings since the last accrual and books one signed `SwapFee` per crossing
  (×3 on the triple weekday). `swap_accrued_until` advances to the canonical `now`. Gated:
  margin only, `swap_mode == POINTS`, swap-bearing market.
- **Deterministic anchor:** the conversion uses the position's **`entry_tick_value`** (fixed
  at open), not a per-tick lazy value — so the swap is independent of *when* the refresh
  runs and is bit-reproducible (the #368 determinism promise). For a quote-currency account
  this is exact; per-rollover-rate conversion is an MT5-calibration refinement (#209).
- **Canonical clock:** all event times come from `executor.get_current_time()` (advanced by
  both the tick and the heartbeat) — never wall-clock (see CLAUDE.md §9 / #375).

### Config

`configs/market_config.json` → `market_rules.forex.swap_rollover` (framework convention, not
broker-exported data):

```json
"swap_rollover": { "local_time": "17:00", "timezone": "America/New_York" }
```

Per-symbol rates + triple weekday come from the **broker** config (raw MT5 export):
`swap_mode`, `swap_long`, `swap_short`, `swap_rollover3days` (MT5 weekday: Sun=0 → converted
to Python Mon=0 via `time_utils.mt5_weekday_to_python`).

### Swap-free accounts (`swap_mode = none`)

Some brokers offer **swap-free** (a.k.a. **Islamic**) accounts: overnight positions accrue no
swap, because Islamic finance prohibits interest (*riba*). The broker usually recovers the cost
another way — a wider spread or a flat per-night holding / administration fee after a grace period
— rather than the interest-differential swap.

In the model this is `swap_mode = none`: the accrual gate (`!= POINTS`) takes the early return, so
`swap_long` / `swap_short` / the rollover config are present in the config but **never applied** —
swap is exactly 0, however long the position is held. `none` is a *supported* mode (the #407
validator accepts it), in contrast to `interest_*` / `percentage`, which are rejected before the run.

> Caveat: the model books literally zero for `none`. A real swap-free account's compensating
> holding / admin fee is **not** modeled here — so for multi-night holds a `none` backtest reads
> slightly optimistic if the broker charges one. A dedicated holding-fee model would be a separate
> cost (future).

### Algo awareness (opt-in)

`DecisionTradingApi` (forwarding to `MarketClock`) lets an opt-in strategy look ahead:

| Method | Returns |
|---|---|
| `get_time_to_next_rollover()` | `timedelta` to the next rollover, or `None` (swap-less market) |
| `is_next_rollover_triple(symbol)` | `True` if the next rollover is the triple day |
| `get_time_to_market_close()` | `timedelta` to the next weekend close, or `None` (24/7) |
| `is_weekend_ahead(within)` | weekend close within the window? |

Accrued swap is also visible passively on the position (`get_swap_cost()`). All of this is
deterministic (config + calendar) — safe for the algo to read at runtime.

---

## Reporting

- **COST BREAKDOWN / portfolio:** `total_swap` (signed) — net debit/credit over the run.
- **Trade history (per trade):** a **Swap** column + per-scenario footer total — each trade's
  signed overnight cost, broken out of `total_fees`.

---

## Testing

| Suite | Covers |
|---|---|
| `tests/framework/market_calendar/` | DST local→UTC, MT5→Python weekday, rollover enumeration (weekend-skip + triple), next-rollover, next-close, MarketClock awareness |
| `tests/simulation/swap_cost/` | end-to-end accrual: long debit, short credit + triple, spot=0, determinism/idempotency |

---

## Limitations (v0)

- **Sim/backtest only.** Live MT5 broker-reported swap reconciliation → #209.
- **`POINTS` swap mode only** (the MT5 model); `NONE` is the swap-free case. `interest_*` /
  `percentage` modes are **not silently skipped** — a symbol declaring one is rejected before the
  run (sim: the scenario is marked invalid and excluded; live: startup abort), so no run ever
  mis-reports financing. Validation: #407. Implementing the remaining modes: #408.
- **Weekend-only calendar** — holiday-aware swap (extra value-date days around holidays) waits
  on the full holiday calendar (#370).
- **`entry_tick_value` anchor** — exact per-rollover rate conversion deferred to #209 calibration.
