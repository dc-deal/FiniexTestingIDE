# Order Precision Tests

Regression guard for the order-precision (price) normalization on the shared
executor layer (`AbstractTradeExecutor`). The first live Field Study (#332)
showed that a raw computed limit price (e.g. an offset-percentage price like
`1896.7294`) was sent to the broker unrounded and rejected (*"price can only be
specified up to N decimals"*). Prices now snap to the symbol's `digits` before
the local book records the order and before the adapter submits it — so
simulation and live round identically.

**Volume is intentionally not normalized:** a step-misaligned lot is a
position-size change, left for `validate_order` to reject as `INVALID_LOT_SIZE`
(broker-accurate, mainstream practice). See the architecture doc.

## What it covers

| Test class | Path | Asserts |
|---|---|---|
| `TestRoundPrice` | `_round_price` | price → N decimals; `None` passes through |
| `TestOpenOrderNormalization` | `open_order` (LIMIT) | resting limit price rounded to `digits`; unknown symbol → graceful reject (no crash) |
| `TestModifyLimitNormalization` | `modify_limit_order` | new limit price rounded to `digits` |

The path tests run through a `TradeSimulator` on Kraken Spot (BTCUSD: `digits=1`)
via a zero-latency INSTANT_FILL mock.

**Parity note:** the same helper runs identically in `LiveTradeExecutor`. The
live broker path cannot be unit-tested, so the shared logic is proven here and
inherited by live (and by MT5 once #209 populates `digits`).

## Run

```bash
pytest tests/simulation/order_precision/ -v
```

Or launch.json: `🧩 Pytest: Order Precision (All)`.

## Design note

`_round_price` is private; the suite mirrors the established sim-test convention
of asserting on executor internals where no public observable exists. Price uses
decimal rounding (`round(price, digits)`); a non-decimal price tick
(futures / indices) would need a `tick_size` snap — none in scope.

See `docs/architecture/architecture_execution_layer.md` → *"Order Normalization:
The Shared Core"* for the implementation.
