# Venue API performance

A live session talks to its venue through the venue's REST API: it places and cancels orders, asks
what they did, and reads its account. How fast and how reliably the venue answered decides how late
a fill becomes known and whether a check reached the venue at all — and a slow venue looks, from the
outside, like a quiet market. This section says how the venue's API answered one session, endpoint
by endpoint.

**Route**

```
GET /api/v1/reports/runs/{run_id}/venue-api-performance
```

**What is not here:** the session's own orders and what each did — the
[order events](/api/v1/docs/order-events), whose `in_flight_ms` says how long one order's answer took;
whether the session's books and the venue's agreed — the [venue account](/api/v1/docs/venue-account).

## One row per endpoint

`endpoints` holds a row for each endpoint the session called, busiest first, keyed
`["venue_endpoint"]` — the answer declares it in `key`; see [row keys](/api/v1/docs/row-keys).

| Field | Meaning |
|---|---|
| `venue_endpoint` | the endpoint, as the session's adapter labels its call — for a REST venue usually the path it called |
| `calls` | the calls the session made to it |
| `avg_ms`, `min_ms`, `max_ms` | how long a call took, from the request to its answer, in milliseconds |
| `slow_calls` | the calls slower than `slow_call_threshold_ms` |
| `failed_calls` | the calls that failed — the venue answered with an error, or the answer never arrived |

Beside the rows, the session's totals and the threshold they were judged against:

| Field | Meaning |
|---|---|
| `slow_call_threshold_ms` | a call slower than this counted as slow — the session's configured threshold |
| `slow_calls` | the slow calls over every endpoint |
| `failed_calls` | the failed calls over every endpoint |

```json
{
  "run_id": "20261201_010500_ab12cd34",
  "slow_call_threshold_ms": 3000.0,
  "slow_calls": 17,
  "failed_calls": 2,
  "endpoints": [
    {"venue_endpoint": "/0/private/QueryOrders", "calls": 41112, "avg_ms": 212.4, "min_ms": 88.1,
     "max_ms": 3810.0, "slow_calls": 9, "failed_calls": 2},
    {"venue_endpoint": "/0/private/Balance", "calls": 2880, "avg_ms": 190.7, "min_ms": 91.3,
     "max_ms": 3402.6, "slow_calls": 8, "failed_calls": 0}
  ],
  "key": ["venue_endpoint"]
}
```

An endpoint is listed once it has been called, so a row always has a call; the averages are over the
whole session, so a slow stretch and a fast one read as their mean — `max_ms` and `slow_calls` say
whether there was a slow stretch at all.

## When there is no answer

The answer is a 404 that names its cause, as for every section — see
[errors](/api/v1/docs/errors). A backtest has no venue API to call. A session writes this section
whenever it watched its venue's API, with no endpoint when its adapter called none; a session that
did not watch it — the setting is the session's — has none, and neither has a session recorded
before this section existed.
