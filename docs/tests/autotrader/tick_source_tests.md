# Tick Source Tests

The venue tick source decides what a tick *is* before anything else sees it — and until #520 step B
it had no offline coverage at all: `KrakenTickSource` and its parser appeared nowhere under
`tests/`, while sitting in the path that feeds a real-money session.

The parser and subscription tests use **recorded frames**, not hand-written ones: those messages
come from `tests/fixtures/tick_sources/kraken_ws_frames.json`, captured verbatim from
`wss://ws.kraken.com/v2` on 2026-09-16 by
`python/experiments/venue_probes/probe_kraken_quote_channel.py`, so what they pin is what Kraken
actually sends, not what we believe it sends. The reconnect-loop tests script their own frames, in
the same shapes, against a local server — what they pin is the connection behaviour.

The reconnect loop is tested against a **WebSocket server on the local machine** (#609): the real
`websockets` library on both ends, each connection scripted — accept and close at once, deliver and
close, go silent, refuse the subscription, drop the TCP connection — with the thresholds shrunk to
fractions of a second. No test opens a connection to the venue. **Not in here:** the arithmetic of
the retry ladder itself, which `ConnectionLadder`'s own suite pins.

| File | Covers |
|---|---|
| `test_kraken_quote_stamping.py` | the parser: which channel produces a tick, the quote stamped on it, and what happens without one |
| `test_kraken_tick_stream_record.py` | the tick stream's own record (#609): one line per outage with the public trade ids around it, the folding of connections that drop before a trade, a restoration lost again leaving none behind, refused trades and their reasons, the first 100 of a reason listed and the totals closing the record, a record that cannot be written or closed, why a connection ended, TLS read off the parsed scheme; and the loop against a local server — the backoff that climbs until a connection delivered, silence aborting a connection to a far side that answers nothing, a trade ahead of its acknowledgement held until the subscription stands and dropped with a refused one, a refused subscription closing its socket, stop() waking a long backoff |
| `test_kraken_quote_subscription.py` | the source: which channels are requested, what a refusal does, and what the CONNECTION panel is told |

## Why a quote at all

Kraken's trade channel reports executions, and an execution happens at exactly one price — so a
trade tick on its own carries `bid == ask` and no spread. The quote it executed against rides the
ticker channel. The collector has stamped it on every tick since format 1.6.0; our Kraken tick source
now does the same, so an archived tick and one from the venue describe the same thing.

The rules under test are decisions rather than style, which is why each has its own case:

- **A trade is never held back waiting for a quote.** Without one, the trade price stands in for
  both sides exactly as before.
- **No quote means no age — `None`, never `0`.** A zero asserts a quote observed in that same
  millisecond, a measurement nobody made.
- **An impossible quote is dropped, not stored.** Crossed or non-positive, the previous quote
  survives and ages visibly instead of failing invisibly. Measured at the venue: 0 crossed and 0 equal in
  202 quotes, so this is a guard rather than a routine event.
- **A quote is never discarded for being old.** A quiet ticker channel is a degraded state, not a
  session-ending one.

## The strategy plane must not move

The case that matters most is `test_the_traded_price_survives_the_quote`. `last` stays the traded
price while `bid`/`ask` become the quote, so `tick.price` — what bars, indicators and decisions
read — is identical before and after this feature. Only `mid` moves, and with it the
valuation plane. That guarantee is pinned rather than argued.

## Bursts

A burst is several fills sharing one exchange timestamp: one market order sweeping through deeper
book levels. Every fill in it carries the same quote, and their prices walk away from the top of
book — depth consumed, not spread paid. `test_every_trade_in_a_burst_carries_the_same_quote` fixes
that the parser states the quote and does not "correct" the prices.

Measured 2026-09-16 over 40 s of BTC/USD: 26 of 40 trades were inside a burst, one sweep walking
nine fills from the bid down to −8.9 against a median spread of 0.10. On the trades that lead a
burst the taker-side convention held **14 of 14** — a BUY prints at the ask, a SELL at the bid.

## Two subscriptions, one connection

Kraken names the channel in each subscription acknowledgement
(`{"method":"subscribe","result":{"channel":"ticker",…},"success":true}`). Without matching on it
the first ack would satisfy both waits, and a refused trade subscription would look like a
successful one — `test_a_ticker_ack_does_not_satisfy_the_trade_handshake` is that case.

The asymmetry between the two channels is deliberate and tested from both sides:

| Channel refused | Result |
|---|---|
| `ticker` | degraded — one session-logger warning, trades continue without a quote |
| `trade` | fatal for the attempt — raises, the outer loop reconnects |

A data-quality improvement must not become a new reason a bot refuses to start.

## What the operator sees

`test_the_panel_distinguishes_every_quote_condition` renders all five states, because a narrow
market and a quote cache that stopped updating look identical if only the spread is shown. The age
beside it is what separates them: a stuck cache shows a frozen spread next to a number that keeps
climbing.

```
Spread:         —  (off)                     quote channel not subscribed
Spread:         —  (waiting for quote)       subscribed, none seen yet
Spread:         0.1  (0.0001%)   quote 214ms live
Spread:         0.1  (0.0001%)   quote 47s   a stuck cache
Spread:         —  (degraded: no ticker)     subscription refused, trades still flowing
```

Presentation only. A colour marks a stale quote; whether one *warrants* a warning is a verdict and
belongs to a validator.

## Running

```bash
pytest tests/autotrader/tick_sources -v
```

Launch entry: `🧩 Pytest: Kraken Tick Source (All)`.
