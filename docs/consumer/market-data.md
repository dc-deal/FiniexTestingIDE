# Market data

A bar is a timestamp, four prices and two counts, and each can be read wrong. Is the stamp the
period's open or its close? Is the price what actually traded, or the midpoint between two quotes
nobody took? Does `v: 0.0` mean nothing traded, or that this feed carries no volume at all? Get one
of those wrong and you have a plausible number that is wrong, and nothing in the rows will say so.
These routes serve the archive this installation holds — which venues, which symbols, what span,
the bars themselves and an indicator over them — and they put in their headers what the rows cannot
carry.

**Routes**

```
GET /api/v1/brokers
GET /api/v1/brokers/{broker}/symbols
GET /api/v1/brokers/{broker}/symbols/{symbol}/coverage
GET /api/v1/brokers/{broker}/symbols/{symbol}/bars
GET /api/v1/brokers/{broker}/symbols/{symbol}/indicators/atr
```

**What is not here:** where the archive is interrupted. Coverage reports a span, and a span with
nothing in the middle still reads as one span — [gaps](/api/v1/docs/gaps) names every interruption
inside it and says what each one was. What a particular run read, and the venue configuration it
ran under, are report sections: [runs](/api/v1/docs/runs) and [broker](/api/v1/docs/broker).

## The walk, from venue to bars

The first four nest, and each answer is the next one's path:

```
/brokers                                        → ["kraken_spot", "mt5"]
/brokers/kraken_spot/symbols                    → [{"symbol": "BTCUSD", "market_type": "crypto"}]
/brokers/kraken_spot/symbols/BTCUSD/coverage    → {"start": …, "end": …, "timeframes": [ … ]}
/brokers/kraken_spot/symbols/BTCUSD/bars?timeframe=H1&from=…&to=…
```

Both lists come from the bar index, so they describe what has been **rendered**, not what was
collected: a venue whose ticks are imported but whose bars are not rendered does not appear in
`/brokers`, and neither do its symbols.

`market_type` is the **broker's**, resolved once and carried on every symbol of that broker. It
says which market rules the venue follows — `crypto` does not close, `forex` does — and it is what
decides the gap categories in [gaps](/api/v1/docs/gaps). A broker that is in the bar index with no
market type configured answers `500 market_type_not_configured`, which is a defect on this side;
see [errors](/api/v1/docs/errors).

`/brokers` needs a token but no grant of its own, because which venues an installation carries is a
fact about the installation. The other four are reached with a grant naming the broker —
`brokers:<broker>` for the symbol list, `bars:<broker>` for coverage, bars and the indicator. What
a grant is made of is in [the server's own routes](/api/v1/docs/server).

## What makes up a bar

| Field | Meaning |
|---|---|
| `t` | the bar's **open**, in unix seconds UTC |
| `o` `h` `l` `c` | open, high, low, close |
| `v` | traded volume — `0.0` on a feed that carries none |
| `tc` | how many ticks were aggregated into this bar |

`v` and `tc` are not alternatives to each other. On a feed with no traded volume, `v` is `0.0` on
every bar and `tc` is the activity measure — it is the one that says whether the market was busy.

Bars are rendered from ticks, and a period with no ticks produces **no bar**. Gaps are omitted,
never zero-filled, so a chart drawn straight from the rows joins across an interruption unless you
read the stamps.

`from` and `to` are ISO-8601 UTC (`2026-01-01T00:00:00Z`); a naive datetime is treated as UTC.
Rows come back oldest first.

## Three things the rows cannot tell you

The bar body is a bare array, so everything *about* the rows travels as a response header. Three of
them carry facts you cannot infer and get no second chance to get right:

| Header | Value |
|---|---|
| `X-Bar-Time-Basis` | `open` — the stamp is the period's start, never its close |
| `X-Bar-Timezone` | `UTC` |
| `X-Bar-Price-Basis` | `order_driven` · `quote_driven` · `unknown` |

`order_driven` means the OHLC is of the **traded** price: the venue has a central order book and
every trade prints at one price. `quote_driven` means it is of the **midpoint** between the two
quotes, because a dealer-quoted venue has no central place where trades happen and prints no traded
price at all. `unknown` means the file was rendered before the stamp existed.

It is a property of the **file**, not of your request and not of the server's configuration. During
a re-render half the archive still holds the previous answer, and a header that declared the
configuration would be wrong for exactly those files.

Reading a midpoint as a traded price gives you a plausible number that is wrong. That is why this
travels with every answer rather than living in a table somewhere.

If you call from a browser, every `X-Bar-*` header is published cross-origin on purpose: a browser
hides every response header that is not safelisted, which made them invisible to the one consumer
that is a browser.

## The row-count headers, and a cap that refuses

| Header | Meaning |
|---|---|
| `X-Bar-Count` | rows in this response |
| `X-Bar-Total` | rows matching the range **before** the cap |
| `X-Bar-Limit` | the cap that was applied |
| `X-Bar-Truncated` | `true` when the range held more than the cap |

`X-Bar-Total` is what makes the rest reachable: it is how many rows `limit` left behind, so you can
narrow the range deliberately instead of guessing.

`limit` is **your** cap, not ours. Omit it and the maximum applies. Ask for more than the maximum —
10,000 — and the request is refused with `400 invalid_limit`, never quietly cut, and the refusal
names the maximum. A cap applied behind your back is worse than a refusal, because the shortened
answer looks complete.

`from` not earlier than `to` is `400 invalid_range`.

## Coverage is the outer span

```json
{"start": "2026-01-25T16:00:00+00:00", "end": "2026-09-18T10:46:37+00:00",
 "timeframes": ["D1", "H1", "H4", "M1", "M15", "M30", "M5"]}
```

`start` and `end` are the **outer** bounds across every rendered timeframe — the earliest start of
any of them, the latest end of any of them. A single timeframe can begin later or end sooner than
the span says, so do not read it as a promise about the timeframe you are about to ask for.

`timeframes` names the ones rendered for this symbol, which is what `/bars` can answer for. Asking
for a timeframe this server knows but has not rendered for the symbol is
`404 timeframe_not_rendered`. Asking for one it does not know at all is `400 invalid_timeframe`,
and the refusal lists the valid names. They are different answers because they need different
things from you.

The list is in **alphabetical** order, not by duration. The server's own timeframe list carries
each name's length in minutes — see [the server's routes](/api/v1/docs/server) — if you need them
ordered.

A symbol that is in the index but holds no bars answers `404 no_bars_indexed`.

## ATR, and the two things it declares

```
GET …/indicators/atr?timeframe=M5&from=<iso>&to=<iso>&period=14&smoothing=rma
```

The answer is one point per bar — `t` on the same basis as a bar's `t`, `v` the indicator's value
there.

**It computes a lead-in.** Wilder's smoothing is recursive, so the value at the first bar you asked
for depends on bars *before* it. Computing only the requested range would hand back a seed instead
of an ATR, and the seed would look like a number. The lead-in is taken by **row count**, never by a
calendar offset, because a calendar window silently under-delivers across a market closure.

**It declares which average smoothed it.** Outside this API "ATR" means Wilder's smoothing, and you
cannot tell from the rows which one produced them:

| Header | Meaning |
|---|---|
| `X-Indicator-Smoothing` | `rma` (Wilder — the default and the standard) · `ema` · `sma` |
| `X-Indicator-Period` | the period the values were computed with |
| `X-Indicator-Timeframe` | the bar timeframe underneath them |

`X-Bar-Time-Basis`, `X-Bar-Timezone` and the row-count headers above travel with it too, since the
points carry the same stamps as the bars they came from. `X-Bar-Price-Basis` does not; ask `/bars`
for the same symbol and timeframe if you need it.

Like `limit`, `period` is refused rather than clamped: above its maximum of 500 it answers
`400 invalid_period`, and the refusal names the maximum.

A bar whose value is not defined yields no point rather than a null one, so the point count can be
lower than the bar count over the same range — which is also why `X-Bar-Total` on this route counts
points, not bars.

## An empty window answers two different ways

A range that matches no bars answers `200` with an empty array on `/bars`, and
`404 no_bars_in_range` on the indicator route. It is the one asymmetry between the two, and worth
handling once rather than discovering.
