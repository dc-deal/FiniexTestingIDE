# Venue account

A live session keeps its own books — the orders it believes are resting, the balance it believes
it holds — and the venue keeps the real ones. Each time the session asks the venue, the answer is
one line on the [order events](/api/v1/docs/order-events) stream. This section reads those lines
for you: what the venue held when the session began, what it held when the session ended, and
whether the session's books and the venue's came apart in between.

**Route**

```
GET /api/v1/reports/runs/{run_id}/venue-account
```

**What is not here:** the venue's orders one by one — they are on the `broker_truth` list of the
[order events](/api/v1/docs/order-events), and each read here names its line there. What the
session's own books say about its money is the [portfolio](/api/v1/docs/portfolio).

## One row per live session

`units` holds a row for each session that asked its venue anything, keyed `["name"]` — the answer
declares it in `key`; see [row keys](/api/v1/docs/row-keys). `name` is the session, as every other
section of the run names it.

## At the start and at the end — `at_start`, `at_end`

Each is one read of the venue, counted:

| Field | Meaning |
|---|---|
| `seq` | the read's line on the order-event stream — the orders are listed one by one there |
| `venue_order_count` | the orders the venue reported as open — all of them, also orders this session did not place |
| `venue_balances` | the venue's balance sheet as it reported it — every asset, under the venue's own asset codes, which need not be the names the symbol uses |
| `venue_position_count` | the venue's positions — a margin account only; null on spot, where a holding is a balance |
| `unread_parts` | the parts the venue could not be read for — `venue_orders`, `venue_balances`, `venue_positions` |

**A part the venue did not answer for is null and named in `unread_parts`** — never `0` or `{}`,
which say the venue holds nothing. A null position count without the name is a spot account.

`at_start` is read once the session has settled what it found at the venue, before its first
market data; `at_end` once the session has handled its own orders at the end. Either is null when
the session never took that read — it stopped before its start, or it was ended before its end.

## In between — the reconciliation

While it runs, a live session compares its books with the venue's on a fixed cadence, and writes a
line only when the comparison's picture changes. It begins from clean, so the first line it writes
is always a divergent one.

| Field | Meaning |
|---|---|
| `reconcile_lines` | the lines the comparison wrote |
| `divergent_lines` | of those, the ones that found the books apart |
| `last_reconcile_state` | `clean` or `divergent` — where the last line left the books; null when none was written |
| `last_divergence` | the latest divergent picture, by identity |

`last_reconcile_state: "clean"` says the last divergence was followed by a clean picture;
`"divergent"` says it still held when the last line was written. Two lines keep a configured
distance — five minutes unless the session sets another — so a change in a session's last minutes
may have no line; `at_end` shows what the venue held then. No line at all says the comparison never
found the books apart, or did not run: a session can switch it off.

`last_divergence` names its members as a divergent line on the order events does: venue
references of orders the session cannot place (`ghost_orders`, `abandoned_orders`,
`foreign_session_orders`), the session's order ids the venue does not show (`orphan_orders`,
`unconfirmed_orders`) or shows differently (`stale_orders`), and positions as counts.

```json
{
  "run_id": "20261007_120000_ab12cd34",
  "units": [{
    "name": "btcusd_session",
    "at_start": {"seq": 1, "venue_order_count": 0, "venue_balances": {"ZUSD": 812.4, "XETH": 0.0031},
                 "venue_position_count": null, "unread_parts": []},
    "at_end":   {"seq": 212, "venue_order_count": 0, "venue_balances": null,
                 "venue_position_count": null, "unread_parts": ["venue_balances"]},
    "reconcile_lines": 2,
    "divergent_lines": 1,
    "last_reconcile_state": "clean",
    "last_divergence": {"ghost_orders": ["OQ3V2K-ABCDE-FGHIJK"], "abandoned_orders": [],
                        "foreign_session_orders": [], "unconfirmed_orders": [], "orphan_orders": [],
                        "stale_orders": [], "ghost_positions": 0, "orphan_positions": 0,
                        "stale_positions": 0}
  }],
  "key": ["name"]
}
```

## When there is no row

The answer is a 404 that names its cause, as for every section — see
[errors](/api/v1/docs/errors). A backtest has no venue account: its venue is its own book, and
there is nothing to ask. Neither has a dry run against a real venue, whose account reads are
answered by the session itself rather than by the venue. And a session from before this section
existed has none until it is run again.
