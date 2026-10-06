"""
The API's CONTRACT version — what a consumer builds against, and what they can assert.

The app version moves on every release; the contract moves when a route or a response model
moves, and the two are not the same clock. Measured 2026-09-22: three models changed shape
inside one app version — `reconciles` became three-state, `run_net_pnl` became nullable,
`segment_trade_count` was removed — and a consumer had no way to see any of it. `/health`
reported the same number before and after.

**Deliberately not a deprecation mechanism.** A deprecation warning promises that the old shape
still works for a while; §27 says this project ships no backward-compatibility layers in the
alpha, so such a promise would be one we never keep. What is offered instead is stronger
because it is true: the server states which contract it IS, a consumer records that number
with its fixtures and asserts it at start-up. A stale mock then fails loudly and locally — no
connection needed, because the number travels IN the response it was captured from.

Bumped BY HAND, in the same change that moves a model. `CHANGES` carries what moved in the
current version, for the consumer deciding whether they care; the full history is
`docs/consumer/contract-log.md`, which the API serves at GET /api/v1/docs/contract-log.
"""

from typing import List

# One monotonic integer. Not a date and not the app version: a consumer compares it for
# equality, and equality is the only question they have.
API_CONTRACT_VERSION = 22

# Every response carries it, so a saved fixture carries it too.
CONTRACT_HEADER = 'X-Api-Contract'

# Where the contract lives in the URL space. Here rather than beside the mount, because it is
# what a consumer writes into their base URL — the version in the path and the version in
# `API_CONTRACT_VERSION` are the same statement at two granularities.
API_PREFIX = '/api/v1'

# What moved INTO the current version. One line per change, written for someone who cannot
# read this repository.
CHANGES: List[str] = [
    'docs: three new routes serve this API\'s own documentation. GET /api/v1/docs lists every '
    'document, GET /api/v1/docs/{name} serves one as text/markdown — the only answer here that is '
    'not JSON — and GET /api/v1/docs/search?q=… ranks their PASSAGES, the text under one '
    'heading, against a query, each hit '
    'naming its document, its heading and the line it starts at. A new grant surface, docs: a '
    'token that enumerates its surfaces is refused until it is re-minted, and the server reads '
    'its tokens at boot, so the grant takes effect on a restart',
    'docs/search: terms_not_matched lists the query\'s words that appear in no document at all — '
    'read it when an answer looks wrong, because a word outside our vocabulary contributes '
    'nothing to the ranking and that is invisible from outside. An empty q is refused with 400 '
    'empty_query rather than answered with everything or with nothing',
    'Every route answers with Link: <…>; rel="describedby", naming the document that describes '
    'it. A header rather than a field, so it reaches the routes whose body is a bare array too, '
    'and so a client that ignores it is unaffected',
    'Link and the seven X-Bar-* headers are published cross-origin. The bar headers were already '
    'served and were invisible to a browser client, which hides every response header that is not '
    'safelisted — three of them carry semantics a caller cannot infer from the rows',
    'Three new error codes: document_not_found (404), empty_query (400) and query_too_long (400)',
]
