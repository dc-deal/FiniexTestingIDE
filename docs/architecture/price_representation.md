# Price Representation — how a price is stored, computed and served

A price arrives from a venue as a decimal — `0.87957` — and every system that keeps it has to
choose a number type for it. The choice decides three things at once: whether a backtest computes
with the same number a live session read, whether a report shows the venue's decimal or a binary
approximation of it, and what every tick costs in disk and memory. This document records how the
industry answers the question, what we measured, and what this project does.

Not here: how a consumer of the API renders a price (`docs/consumer/prices.md`, served as
`/api/v1/docs/prices`) and the import mechanics (`docs/data_pipeline/`).

## How the market answers it

Venues and engines split into two schools.

**Decimal on the wire, precision declared beside it.** Kraken's REST and WebSocket APIs send
prices and volumes as decimal strings and declare each pair's tick size and decimals separately;
Binance (`tickSize` in its price filter) and Coinbase (`quote_increment`) do the same, and FIX
carries prices as decimal text with the instrument's minimum price increment beside them. Engines
of this school keep the decimal: LEAN computes in C# `decimal`, NautilusTrader stores prices and
quantities as fixed-point integers with a precision.

**A double, and the instrument's digits beside it.** MetaTrader 5 hands out prices as `double`
with `SYMBOL_DIGITS` and `SYMBOL_TRADE_TICK_SIZE`; the Interactive Brokers API does the same with
`minTick`. The double is the price, and the declared step says how to round it for display or for
an order.

There is no consensus, and both schools work: what matters is that a system knows which of its
numbers are the venue's and which it computed.

## What the books say

- Robert C. Martin, *Clean Code*, ch. 17, heuristic G26 "Be Precise": using floating point for
  currency is "almost criminal". This sentence started our first measurement.
- Yves Hilpisch, *Python for Finance*, ch. 3: the `decimal` module gives exact decimal arithmetic,
  at a cost that matters in a loop.
- Martin Kleppmann, *Designing Data-Intensive Applications*, ch. 4: JSON tells integers and
  floating-point numbers apart by nothing and fixes no precision, so the decimal a venue sends is
  only as exact as the reader's parser.
- Barry Johnson, *Algorithmic Trading and DMA*, and Larry Harris, *Trading and Exchanges*: the tick
  size is a rule of the market, not a property of a number — the place where rounding belongs.

## What we measured

- **2026-09-12, float32 resolution against the venue's step.** At 88,000 USD a float32 resolves
  0.0078, while BTCUSD's smallest price step is 0.09375 — twelve times coarser, and no observed
  price movement fell within one unit of resolution. float32 storage was judged safe, with a trigger
  to re-open it near a unit price of 200,000.
- **2026-10-17, what float32 cost anyway.** Every served price came back widened —
  `0.8795700073242188` for `0.87957` — because each consumer read the float32 as a double. And a
  decimal with more than about seven significant digits had lost its last one: early external
  history prices (`133.85816` stored as `133.85815`), Kraken volumes with eight decimals
  (`1.47741803` as `1.4774181`).
- **Could the stored values be repaired instead of re-imported?** The shortest decimal of each
  float32 restores the venue's decimal wherever float32 did not round it away. Over 2.76 million
  ticks of external history: 12.33 % of prices and 9.78 % of volumes were exact as stored; a repair
  would have reached 99.998 % of prices but only 62.3 % of volumes. Our own collector's prices
  would have come back complete, its Kraken volumes at 79 %.
- **What float64 costs.** A tick file grows by 0.3–0.4 %, because Parquet stores the repeating
  prices as a dictionary; an in-memory frame holds 16 more bytes per tick.
- **What `Decimal` would cost.** 424 ns per operation against 1.4 ns for a numpy float64, for a
  difference of about 2·10⁻¹¹ in a P&L.

## What this project does

- **The archive stores the double the venue's decimal parses to.** Prices and volumes are float64
  from the import on — the same number a live session reads, because the live side parses the same
  decimals into the same type. Only `spread_pct`, a ratio no surface serves as a price, stays
  float32.
- **The archive was re-imported in full from its raw files on 2026-10-17** rather than repaired,
  because a repair could not restore what float32 had already rounded away. A run made before
  that date read the float32 values, and only its start time says so: the data format version it
  records did not change, because a run records the kind and count of the files it read, not
  their content
  ([A run is a record, and an old one stays true](../introduction_to_the_ide.md#a-run-is-a-record-and-an-old-one-stays-true)).
  Run again, it reads the exact decimals and can come out different in the last digits — a
  difference of the input, not of the code.
- **No `Decimal`, no fixed-point.** float64 holds 15 to 17 significant digits — far more than any
  instrument we trade quotes — at the cost of the hot path's native type.
- **A computed price keeps full precision**; it is rounded for display by the instrument's tick
  size, never before it enters another computation.

## When to re-open it

- A venue quotes or sizes an instrument with more significant digits than a double holds exactly —
  a price of a billion with a step of 10⁻⁸ would be the first.
- A figure has to equal a venue's own decimal ledger to the last unit over very many operations,
  such as reconciling an account balance fill by fill. Then the decimal belongs at that boundary,
  the way the fixed-point engines keep it everywhere.
