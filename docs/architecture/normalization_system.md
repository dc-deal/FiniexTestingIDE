# Normalization System

`Normalizer` (`python/framework/utils/trading_math/normalizer.py`) is the single, audited path for
turning a price-space quantity into a **dimensionless, cross-instrument-comparable ratio**.
Workers and indicators normalize through it instead of re-implementing the math inline.

## Why normalize

A raw indicator value — a price distance, a slope in price units — is not comparable across
instruments. A `0.1%`-per-bar move is a storm for EURUSD and a calm day for BTCUSD; an
absolute price distance of `2.30` means nothing without the instrument's scale. Dividing a
price-space quantity by a **local volatility reference** (rolling std, band width, ATR)
yields a value that means the same thing on any instrument and at any price level.

The established platforms normalize rather than split indicators per asset class — there is
no `SMA_forex` vs `SMA_crypto`. The same idea is formalized in `sklearn.preprocessing`
(`StandardScaler` = z-score, `MinMaxScaler` = range rescale), in Bollinger's own `%B` /
`BandWidth`, in CCI (deviation over mean-deviation), and in volatility-scaled momentum
(managed-futures / time-series-momentum practice).

## API

Stateless static methods:

| Method | Formula | Use |
|--------|---------|-----|
| `rescale(value, lower, upper)` | `(value - lower) / (upper - lower)`, unclamped; `upper <= lower → 0.5` | %B / MinMax position within a range (overshoot preserved) |
| `clamp(x, low=0.0, high=1.0)` | bound to `[low, high]` | the clamped companion of `rescale` |
| `normalize(value, scale)` | `value / scale`; `scale <= 0 → 0.0` | volatility-unit slope (delta/std) and relative magnitude (width/level) |

`normalize` is the heart of the system: one safe divide that expresses a quantity in units
of a reference scale. A non-positive scale (flat / undefined volatility) returns `0.0`.

## When to normalize?

Normalization applies to a **price-space quantity measured against a volatility or range
reference** — not to every number a worker produces. The CORE workers illustrate the
recurring cases:

| Worker | Normalize? | Why |
|--------|-----------|-----|
| Bollinger | Yes | `%B` (position in band), slope-over-band-width, relative width are all values relative to the band / local volatility |
| Moving-average trend | Yes | slope measured in volatility units (delta / std) |
| RSI | No | already a bounded `0–100` oscillator by construction — its `avg_gain / avg_loss` ratio has different zero-semantics, not a volatility scaling |
| MACD | No | raw price differences (`fast_ema − slow_ema`) — there is no normalization step |
| OBV | Situational | its trend test scales a change by mean volume; route through `normalize` only if the zero/edge behavior stays correct |

> **Disclaimer:** this table is not exhaustive — it lists a few workers as **orientation**
> for how to decide, not a registry. The rule of thumb: if you are expressing "how far / how
> steep / how wide" *relative to volatility or a band*, normalize; if you are producing a
> bounded oscillator or a raw difference, you are not.

## Consumers

- **Bollinger worker** (`bollinger_worker.py`): `position_raw = rescale(price, lower, upper)`,
  `position = clamp(position_raw)`, `slope = normalize(midline_delta, band_width)`,
  `width_pct = normalize(band_width, middle)`.
- **ma_trend worker** (`ma_trend_worker.py`): `slope = normalize(ma_delta, std_window)`,
  `volatility_pct = normalize(std_window, ma_value)`.

Both workers reason in one consistent "volatility-units" space, so a strategy can share
thresholds (e.g. a neutral band, a falling-knife slope cap) across the H1 trend gate and the
short-timeframe band — and across instruments.

## Boundary

`Normalizer` is pure math on values passed in. It never reads market state, caches, or
volatility profiles — that would couple a deterministic computation to mutable external data
and break reproducibility. Volatility *references* are computed locally by the caller (the
worker's own window), never pulled from a precomputed profile at runtime.

A runnable illustration lives at
`python/experiments/normalization_demo/normalization_demo.py` (launch entry
"📐 Normalization Demo"): the same trend on EURUSD vs BTCUSD — raw numbers incomparable,
normalized identical.
