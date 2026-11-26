# How a market is described: three independent axes

A backtest that disagrees with a live-adapter session about what "the price" was is not a backtest.
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
+ `spot` + `order_driven`. Keying the price basis off the asset class would render every bar of
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

- ✅ **Real-money-proven.** A real-money acceptance certificate exists for this combination.
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
| **Resting orders** — when a stop fires, when a resting limit is met | the venue's declared rule | A venue that prints trades decides on them; one that does not, on the quote. See below |

### When a stop fires and a resting limit is met

The venue decides, and it declares how on its adapter's `OrderCapabilities`: `stop_trigger_basis`
(`last` or `bid_ask`) and `limit_fill_evidence` (`public_trade` or `quote`). The simulation executor
and the dry-run simulator read the declaration once and hand it to the one predicate module,
`utils/trading_math/price_trigger.py`, with every tick's traded price — so a backtest and a
rehearsal fire and fill where the venue would.

```
quote 49,994 / 49,996 · last trade 50,000
a long's stop loss 49,995    bid_ask: fires — the bid 49,994 is below it
                             last:    not yet — fires once a trade prints at or below 49,995
resting buy limit 49,990     quote:        filled once the ask falls to 49,990
                             public_trade: also filled once a trade prints below 49,990
```

- **Kraken declares `last` and `public_trade`**, from its own documentation (read 2026-11-24): its
  stop's `trigger` defaults to the last price, and its central limit order book matches by price and
  time. A trade only AT a limit's price fills the orders queued before it, and on a market whose
  spread is one tick most of the time — Kraken BTCUSD's is on 92 % of its ticks — that queue is long.
  So only a trade THROUGH the price proves the order was met, and the backtest errs the cautious way.
- **MT5 declares `bid_ask` and `quote`**, provisionally from MetaQuotes' own documentation (read
  2026-11-24); #209 measures it on a demo account. Its forex ticks carry no traded price at all.
- **Without a traded price the quote decides, whatever is declared.** That is every MT5 tick.
- **A Kraken tick recorded before collector format 1.6.0 carries its trade as its quote** (bid = ask
  = the trade), so there the trade decides, as on every other Kraken tick: its quote at a limit is
  a trade AT it, which fills the queue ahead, and the limit waits for a trade through it. Where that
  quote is modelled around the trade (the modelled quote, on by default), the same holds: a stop
  fires once a trade reaches it, half a modelled spread after the modelled bid or ask would have,
  and a limit fills on a trade through it before the modelled quote reaches it. A backtest of that
  history therefore moves with the venue's rule, the way Kraken would have filled.
- **The quote crossing a limit fills it under both rules** — a book that crosses the order would
  have matched it. A limit arriving at the venue, and a stop-limit's limit at the instant its stop
  fires, are judged on the quote alone, as a taker: the trade that tick carries printed before the
  order stood in the book. A public trade counts from the next tick, for a limit that actually
  rests. The dry run does the same, poll by poll.
- **A triggered stop fills at the market either way**, on the quote.
- **What a strategy keeps itself** — `own_exit_levels`, a level it closes at market — reads the
  quote: that is the strategy's reading of the price, not an order the venue holds.

**The fill row IS the cost model for a quote-driven venue, and that connection has to be stated
here or it gets made twice.** A dealer's revenue is the spread; crossing it on entry and again on
exit means one full spread width is already inside `gross_pnl` before any fee is looked at. So an
MT5 broker books no per-side fee — not because its cost is zero, but because this row already
charged it. Booking a `SpreadFee` beside it charged the same quantity twice, exactly, for as long
as the execution layer has existed. The fee side of the same statement lives in the
[execution layer](architecture_execution_layer.md) and in the
[broker config guide](../broker_config_guide.md); each page used to carry one half and neither
named the other.

On an order-driven venue `bid` and `ask` are a real quote only where one was recorded. Kraken's
trade channel alone reports executions, so a tick built from it carries `bid == ask`; the quote
arrives on a separate channel, which the collector has read since format 1.6.0 and our Kraken tick
source reads too (#520 step B). Before that boundary a Kraken tick has no spread to speak of, and
that is a property of the recording rather than of the market.

`TickData.price` resolves this from the data itself: the traded price where one is present,
the midpoint where it is not. No component reads configuration to decide, and no strategy
needs to know which venue it is running on.

That works on one condition, and it is worth stating because it is easy to break: an absent
traded price must arrive as **nothing**, never as a zero. A zero is a price to everything
downstream, and it would render an entire quote-driven archive at zero without any check
noticing — comparing a high against a low is satisfied by `0 >= 0`.

### Why the traded price, and where the midpoint stands in

A print and a midquote are different quantities. The traded price is where an order met the book;
the midquote lies halfway between the best bid and ask at that moment, and the two differ by the
liquidity premium the trader paid (Harris, *Trading and Exchanges*, 2003, §21.3.1.1, printed
p. 425). Prints also bounce between the bid and the ask from one trade to the next, which gives
their changes a negative serial correlation the midquote does not have (Roll's model, the same
book, §20.3, printed p. 416). So an order-driven market's bars are built from what traded — the
number the venue's own chart and its OHLC endpoint show.

For a quote-driven market, where nothing prints centrally, the midpoint is **this project's chosen
basis, not settled practice**. MetaTrader 5 charts forex from the bid, other platforms let the user
pick bid, ask or midpoint, and NautilusTrader declares each bar type's basis as one of BID, ASK, MID
or LAST (its data concepts page, read 2026-11-24). Midpoint bars set beside a venue's own bid bars
differ by half a spread.

The tick reader derives the price once for every consumer. The two that build from it — the bar
import and the backtest's data preparation — refuse a file holding a tick whose price no bar may be
built from: a price that is not above zero, or a midpoint of a quote with a side at zero, which is
positive and still wrong. A backtest then refuses every scenario of that symbol and runs the others;
a bar import renders that symbol from none of its files and keeps the bars it had. The reader
itself refuses nothing, so a caller that reads the quote as data — the divergence report skips a
tick without a bid on its own terms — still gets the file as it is. Measured over the whole
archive when the check was added (2026-11-24), no file failed it.

## Where the declaration is checked

A declaration that nothing verifies is a comment. `price_formation` is read where it can be
checked or recorded, and never in the event loop:

- **At import.** A venue declared order-driven must deliver a traded price on every tick; a
  file that does not is refused by name rather than falling back quietly to the midpoint,
  which would change what its bars mean without anyone noticing.
- **When a bar file is written.** The price basis is stamped into the file and carried into the bar
  index, so one file answers whether the whole archive was rendered consistently. The HTTP
  API reports the price basis from that stamp rather than from configuration — during a re-render,
  configuration describes what a render *would* produce while half the files still hold the
  previous answer.
- **When a run is recorded.** A finished run states which price basis its bars were rendered from,
  per scenario in the run report and per run in the results ledger. It reads the stamp, not
  the configuration, and a scenario that mounted no bar file records nothing rather than
  borrowing the declaration. The one exception is a live-adapter session: it renders its bars at
  runtime, so there is no file to stamp and the declaration is all there is — which is why the
  run record also says whether its input was an archive or a stream. Without that pair the
  parity proof could not compare the live-adapter session's price basis against the backtest's at
  all.

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
