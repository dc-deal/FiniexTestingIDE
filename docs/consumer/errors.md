# What a refusal says

A status code alone tells you what you already have. One absence usually has several causes, and
each needs a different thing from you — retry, re-authenticate, pick another run, or stop and
report a defect. So every refusal names its **cause**, and there is no bare `not_found` anywhere
in this API.

```json
{"error": "symbol_not_found", "detail": "No symbol 'XYZ' for broker 'mt5' in the bar index"}
```

Every refusal is structured JSON. A traceback never reaches you.

## `error` is for a program, `detail` is for a person

Branch on `error`. It is a stable, closed vocabulary: the table below is the whole of it.

`detail` you may show as it comes. It is written for whoever reads the answer — it names what
happened and, where the remedy is a setting, the setting. It never names a field of a response or
the code behind it, which is what would make it a sentence for a developer rather than for a
reader.

Two refusals carry a header you need:

- `401 unauthenticated` carries `WWW-Authenticate: Bearer`
- `429 rate_limited` carries `Retry-After`

If you call from a browser, both are visible cross-origin — a browser hides every response header
that is not safelisted, so they are published deliberately.

## The vocabulary

| HTTP | `error` | Cause |
|---|---|---|
| 400 | `invalid_timestamp` | a report filter is not ISO-8601 |
| 400 | `invalid_timeframe` | the timeframe is not one this server carries — `/api/v1/timeframes` lists them |
| 400 | `invalid_limit` | `limit` is outside the route's range — refused, never clamped |
| 400 | `invalid_period` | an indicator period is outside its range |
| 400 | `invalid_range` | `from` is not earlier than `to` |
| 400 | `empty_query` | a documentation search was asked for with nothing to search for |
| 400 | `query_too_long` | a documentation search is longer than the cap |
| 401 | `unauthenticated` | no token, or one this server does not know — carries `WWW-Authenticate: Bearer` |
| 403 | `forbidden` | the token holds no grant for this surface or this name |
| 404 | `run_not_found` | no such run in the run index |
| 404 | `reports_not_commissioned` | the run was started without report artifacts |
| 404 | `run_not_completed` | the run has no report artifact yet — still running, or it ended before its report phase |
| 404 | `artifact_not_produced` | the run persisted other sections but not this one |
| 404 | `config_snapshot_missing` | the run declares a configuration snapshot that was never filed |
| 404 | `run_header_missing` | the run index lists the run, but its header file is missing |
| 404 | `broker_not_found` | the broker is not in the bar index |
| 404 | `symbol_not_found` | the broker has no such symbol in the bar index |
| 404 | `no_bars_indexed` | the symbol is indexed but holds no bars |
| 404 | `timeframe_not_rendered` | no bars exist for that timeframe |
| 404 | `coverage_report_unavailable` | the coverage cache holds no report for the symbol yet |
| 404 | `no_bars_in_range` | bars exist, but none in the requested window |
| 404 | `deployment_not_found` | no such deployment in the run-results ledger |
| 404 | `sweep_not_found` | no such sweep in the run-results ledger |
| 404 | `config_file_not_found` | the configuration directory lists no such file |
| 404 | `document_not_found` | no document goes by that name — `/api/v1/docs` lists them |
| 409 | `artifact_unreadable` | a report artifact exists but no longer matches its model, usually its age |
| 429 | `rate_limited` | too many attempts — carries `Retry-After` |
| 500 | `market_type_not_configured` | a broker in the bar index has no market type configured — a defect on this side |
| 500 | `identity_unbound` | a verified token is bound to no account — a defect on this side |

## The four 404s of one run, and why they are four

Ask a run for a report section it does not have and the answer could mean four different things.
Each is a different code, because each needs something different from you:

```
run_not_found           → the id is wrong, or the run was pruned.      Check the run index.
reports_not_commissioned→ the run was never meant to write reports.    Nothing to wait for.
run_not_completed       → it is still running, or it died first.       Retry later, or give up.
artifact_not_produced   → it wrote others, not this one.               Read `artifacts` first.
```

The last one is the one to design against: the two pipelines produce **different** sets of
sections, so a client that assumes a fixed list gets a 404 for the difference. The run index
carries `artifacts` for exactly this reason — read it, then ask only for what is there. See
[runs](/api/v1/docs/runs).

## Two codes mean a defect on this side

`market_type_not_configured` and `identity_unbound` are `500`s, and they are not your doing.
Neither is reachable by sending a different request. If you see one, it is worth reporting.

## What is not in this table

A malformed request that never reaches a handler — a query parameter of the wrong type, a missing
required one — is answered by the web framework itself with `422` and a different body shape.
Those answers do not carry an `error` field today. Branching on `response.error` without checking
the status first will therefore fail on a `422`; check the status, and treat `422` as "the request
was not well-formed".
