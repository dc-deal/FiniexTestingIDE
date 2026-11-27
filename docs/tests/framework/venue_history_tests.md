# Venue History Tests

`tests/framework/venue_history/` — what a venue itself records, read from Kraken: the public trade
list, which confirms the trades our archive lacks (#127's fetch stage), and an account's ledger at
the venue, which shows every movement of its balances (#512).

Run: `python -m pytest tests/framework/venue_history/ -v`
Launch entry: `🧩 Pytest: Venue History (All)`

---

## What this suite exists to catch

The divergence report judges a run against the venue's own record. A reader that answers nothing
where it could not read makes a stretch look as if no trade happened in it, and a ledger look as if
nobody touched the account. Every test runs against recorded answers in the shapes measured on
2026-11-09, with invented values: no test reaches the network, and none reads a real key.

| Failure | What it costs |
|---|---|
| A signature Kraken does not accept | Every private read is refused |
| A nonce that does not rise within one millisecond | Kraken refuses the second request |
| Pages joined without removing the trade both repeat | A trade counted twice |
| A stretch's first trade left out by Kraken's exclusive `since` | A run reads one trade short |
| A refusal or a give-up answered with an empty list | A gap or a movement of the account disappears |
| A ledger trade without the order behind it | The bot's own trade reads as a change of the account |
| A Kraken asset code kept as it is | A movement of `ZUSD` is not seen as one of `USD` |

## Test files

### `test_kraken_venue_readers.py`

| Test | Description |
|------|-------------|
| `TestTheClient` | a private request is signed as Kraken's documented example; its nonce rises strictly; a public client refuses a private read; an empty key is refused before anything is sent |
| `TestThePublicTradeList` | pages join by trade id and the stretch keeps its edges; our symbol is named by Kraken's pair; a venue not answering now is asked again; a refusal raises, never an empty list |
| `TestTheLedger` | pages by offset and names the order behind each trade, in our currency codes; trades are named twenty at a time; a key without the right raises |
| `TestTheKindsOfEntry` | every kind of entry but OTHER has a Kraken type; only a trade can belong to an order |
| `TestTheSeam` | Kraken has both readers; a forex broker has none; an unknown broker type is refused |
