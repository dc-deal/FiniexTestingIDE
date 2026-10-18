# How a price is read

Two kinds of price reach you, and they need different handling. A price the venue quoted or
traded is served as the decimal the venue wrote: `0.87957`. A price this server computed — a
midpoint, a mark, a P&L, a quote it modelled — keeps every digit its arithmetic produced, such as
`1.0800599999999999`, and is yours to round for display. Telling the two apart keeps a screen from
showing digits that mean nothing, and keeps a renderer from rounding a figure that was exact.

Not here: which fields a route serves (each route's own document has them) and how an absent
figure is rendered ([nulls](/api/v1/docs/nulls)).

## A price the venue wrote

Served as a double whose shortest decimal form is the venue's own, so the answer's text already
reads `0.87957`:

- a tick's `bid`, `ask` and `last`, and its `real_volume`;
- a bar's open, high, low and close where the bar's price basis is `order_driven` — each is the
  price of one trade ([market data](/api/v1/docs/market-data));
- a fill at the market where the quote it crossed was recorded;
- what a live session reads from its venue — a balance, a fill price, a fee.

Print them in the shortest decimal form your language offers; JavaScript's `String(x)` does.

## A price this server computed

Everything derived by arithmetic keeps full precision:

- a bar of a `quote_driven` venue, drawn from the midpoint between bid and ask;
- the marks, equity and drawdown, which value an open position at the midpoint;
- every P&L, fee, spread cost and average;
- a quote modelled for data recorded without one, and a fill against it;
- a limit or stop level a strategy computed, and a fill at it.

Round such a value for display only — a price to the instrument's `tick_size`
([broker](/api/v1/docs/broker)), money to its currency's smallest unit. Never round before
comparing or summing: a rounded figure no longer adds up with the rows it came from.

## Runs recorded before contract 34

Until 2026-10-17 the tick archive held prices as 32-bit floats, and a run read them widened:
`0.8795700073242188` where the venue wrote `0.87957`. The archive was re-imported from its raw
files that day. A run recorded before keeps the values it read, because it describes the data it
ran on. Rounded to `tick_size`, nearly all of its prices show the venue's decimal again; a price
with more than about seven significant digits may differ in its last one.
