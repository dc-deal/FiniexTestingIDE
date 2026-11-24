# Resting Order Venue Rule Tests

`tests/framework/test_resting_order_venue_rule.py` — the simulation executor fires a stop and fills a
resting limit by the rule its venue declares, and the dry-run simulator fills on the same tick (#605).
Runs under the synthetic `framework/_root` suite.

## Why this file exists

`test_price_trigger.py` pins the predicate; this file pins that the executor hands it the venue's
declaration and every tick's traded price. Kraken triggers a stop on its last traded price and takes
a public trade THROUGH a resting limit's price as proof the order was met. A trade counts only for a
limit that already rests: the one its arrival tick carries printed before the order stood in the
book. The dry-run simulator plays the same venue, so it is driven over the same ticks and has to fill
on the same one, at the same price. A fill price reaches the portfolio, so the fill cases run in both
account models.

```
quote 49,999 / 50,001 · last trade 50,000
resting buy limit 49,990: a trade at 49,990 leaves it resting, one at 49,989 fills it at 49,990
a long's stop loss 49,995: the bid below it does not fire it, a trade at 49,995 does
```

## What it checks

| Class / test | What it validates |
|---|---|
| `TestTheVenueDeclaresItsRule` | the mock adapter of a kraken_spot profile declares exactly what `KrakenAdapter` declares (`last`, `public_trade`) — the mirror cannot drift; a mock of a quote-driven venue keeps the quote's rule |
| `TestARestingBuyLimitOnKraken` | a trade only at the limit leaves it resting, one through it fills it at its own price; on a quote venue the same trade leaves it resting — both account models |
| `TestALongsStopLossOnKraken` | the bid below the stop does not fire it, a trade at the stop does and the close sells at the bid; a tick without a traded price fires it on the bid — both account models |
| `TestALimitArrivingAtTheVenue` | the trade through the limit on its arrival tick leaves it resting and the next one fills it; a quote already through it on arrival fills it at once, at its own price |
| `TestARestingBuyStopOnKraken` | a resting STOP order, not a position's level: the ask through it does not fire it, a trade at it does, and it buys at the ask |
| `TestTicksWhoseQuoteIsTheirTrade` | on a flat tick (bid = ask = the trade, before collector format 1.6.0) the quote's rule fills a resting limit on the touch, Kraken's waits for a trade through it; on a quote modelled around the trade an ask at the limit implies a trade through it, so Kraken's rule fills one tick before the quote's |
| `TestTheDryRunFillsWhereTheBacktestDoes` | one tick stream per case — a trade through on arrival, a trade at the limit, a sweep, a tick without a trade, a buy stop the ask crosses first, a stop a trade crosses on arrival — fed to the simulation and to the dry-run simulator: the same fill tick and the same price |

## Running

```bash
pytest tests/framework/test_resting_order_venue_rule.py -v --tb=short
```
