# How a market is described: three independent axes

A backtest that disagrees with a live session about what "the price" was is not a backtest.
Getting that right needs three separate facts about a venue, and the trap is that they look
like one — this project trades exactly two venues, and those two happen to differ on all
three axes at once.

**What is not in this document:** how a spread is modelled for data that has none, how bars
are aggregated, and how a fill is priced. This is only about which facts describe a market
and what each one decides.

## The three axes

| Field | Question | Values | Decides |
|---|---|---|---|
| `market_type` | Which asset class? | `forex` · `crypto` | Weekend closure, pip mode, session bucketing, the activity metric |
| `trading_model` | How is a position financed? | `margin` · `spot` | Balance tracking, order validation, whether a "position" exists at all |
| `price_formation` | How do prices come about? | `order_driven` · `quote_driven` | Whether a traded price exists, what a bar is rendered from, what a strategy reads as the price |

All three live on the broker entry in `configs/market_config.json`. The first two have been
there for a long time; the third was added once a venue started reporting a real spread, at
which point "the price" stopped being one number.

### Why price formation is its own axis

The vocabulary is standard market microstructure (Harris, *Trading and Exchanges*, ch. 5–6).

**Order-driven** — a central limit order book matches buy orders against sell orders. Every
trade *prints* at one price, so a "last traded price" is a real event, real per-trade volume
exists, and the venue's own charts are built from trades.

**Quote-driven** — a dealer quotes a bid and an ask and takes the other side itself. There is
no central place where trades happen, so there is no last traded price to report: an MT5
forex feed writes `0.0` in that field on every tick, and that is an absence rather than a
gap.

It is a property of the **venue**, not of the asset class and not of the account model. A
crypto CFD at an MT5 broker is `crypto` + `margin` + `quote_driven`; Kraken spot is `crypto`
+ `spot` + `order_driven`. Keying the bar basis off the asset class would render every bar of
that CFD at zero.

The distinction was already present in the project twice before it had a name, which is the
best evidence it is real: `market_rules.<type>.primary_activity_metric` reports volume where
trades print and a tick count where they do not, and the tick-collector EA gates real volume
on the terminal's own exchange-instrument flag. Real volume and a traded price are siblings —
both exist only where there is a central venue.

## What exists, and what this project supports

All eight combinations occur in the industry. This project trades two of them, diagonally
opposite, which is why the axes look like one until you list them out.

| market_type | trading_model | price_formation | Real example | Support |
|---|---|---|---|---|
| crypto | spot | order_driven | Kraken spot, Binance spot | ✅ |
| forex | margin | quote_driven | An MT5 retail broker | ◐ |
| crypto | margin | order_driven | Kraken margin, Binance futures | ○ |
| crypto | margin | quote_driven | A crypto CFD at an MT5 broker | ○ |
| crypto | spot | quote_driven | A brokered crypto app quoting its own price | ○ |
| forex | margin | order_driven | CME FX futures, an FX ECN | ○ |
| forex | spot | quote_driven | A bank's spot desk facing a corporate | ○ |
| forex | spot | order_driven | Institutional spot FX over an ECN | ○ |

**Legend — each mark claims exactly what it says and nothing more:**

- ✅ **Live-proven.** A real-money acceptance certificate exists for this combination.
- ◐ **Simulation-proven.** The backtesting pipeline runs it end to end; there is no live
  adapter yet.
- ○ **Structurally ready.** The data model, the configuration and the import path carry it,
  and nothing needs to change to describe such a venue. **Nobody has run it** — this is an
  invitation to try, not a promise that it works.

The margin side deserves its own warning regardless of the row: everything measured in this
project so far comes from a spot account, so a defect on the margin path survives a green
test suite and surfaces when real money is on it.

## Which price a component reads

Two prices exist once a venue has a spread, and the difference is not cosmetic.

| Plane | Reads | Why |
|---|---|---|
| **Strategy** — bars, indicators, decisions | `tick.price` | What actually traded, where the venue prints trades. This is what the venue's own chart shows, and what its historical OHLC endpoint returns |
| **Valuation** — equity, drawdown, the circuit breaker, the mark price | `tick.mid` | Marking a position to the last print is one-sided by construction — the print is the ask on a buy — and it is the easiest number on an exchange to push. Every venue and every risk system marks to the midpoint |
| **Slippage baseline** — what a fill is measured against | `tick.mid` | A benchmark has to be neutral between the two sides |
| **Fills** | `bid` / `ask` | A buy pays the ask and a sell receives the bid, unchanged |

On an order-driven venue `bid` and `ask` are a real quote only where one was recorded. Kraken's
trade channel alone reports executions, so a tick built from it carries `bid == ask`; the quote
arrives on a separate channel, which the collector has read since format 1.6.0 and the live tick
source reads too (#520 step B). Before that boundary a Kraken tick has no spread to speak of, and
that is a property of the recording rather than of the market.

`TickData.price` resolves this from the data itself: the traded price where one is present,
the midpoint where it is not. No component reads configuration to decide, and no strategy
needs to know which venue it is running on.

That works on one condition, and it is worth stating because it is easy to break: an absent
traded price must arrive as **nothing**, never as a zero. A zero is a price to everything
downstream, and it would render an entire quote-driven archive at zero without any check
noticing — comparing a high against a low is satisfied by `0 >= 0`.

## Where the declaration is checked

A declaration that nothing verifies is a comment. `price_formation` is read in two places,
neither of them in the tick loop:

- **At import.** A venue declared order-driven must deliver a traded price on every tick; a
  file that does not is refused by name rather than falling back quietly to the midpoint,
  which would change what its bars mean without anyone noticing.
- **When a bar file is written.** The basis is stamped into the file and carried into the bar
  index, so one file answers whether the whole archive was rendered consistently. The HTTP
  API reports the basis from that stamp rather than from configuration — during a re-render,
  configuration describes what a render *would* produce while half the files still hold the
  previous answer.

## Known limit

The declaration resolves per **broker**. MT5 resolves the same question per **symbol**, and
an MT5 broker can serve exchange-traded stocks and futures beside quote-driven CFDs in one
terminal. Every MT5 symbol this project pulls is quote-driven forex, so broker scope is
sufficient for the data that exists — a broker mixing both would need per-symbol resolution.
Tracked with the MT5 live-adapter work.

## Related

- [Data Import Pipeline](../data_pipeline/data_import_pipeline.md) — where a tick becomes a
  parquet and a bar, and what the archive guarantees about a bar file
- [Data Storage Layout](data_storage_layout.md) — bars are derived and deletable, which is
  what makes a re-render cheap
