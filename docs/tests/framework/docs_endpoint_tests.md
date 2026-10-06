# Documentation Endpoint Tests

The API serves its own documentation, and every route names the document that describes it. Four
of the failures that costs are **silent** — the route still answers, with the wrong thing — so
each is a gate rather than something a review is expected to catch.

**Suite:** `tests/framework/api/test_docs_endpoints.py`
**Runner:** `🧩 Pytest: API Endpoints (All)` or `pytest tests/framework/api/test_docs_endpoints.py -v`

Fixtures are module-scoped: one mounted application and one client for the whole file. Mounting
reads the served documents and generates the OpenAPI schema, and neither depends on the test.

## The four silent failures

**A route mounted without naming its document** looks exactly like one that needs none. The walk
reads the generated schema, which is the only enumeration carrying each route's full path, so a
route added tomorrow is covered the moment it exists — and the document it names must be a file
on disk.

**`/docs/{name}` matches `/docs/search`.** Declared in the wrong order, a search answers
"no document called search". That is a 404, it looks like a missing document, and nothing about it
says the routing is wrong. One request pins the order.

**The search must index nothing the document route would refuse**, or a hit names a document that
cannot be fetched. Both directions are checked, and every hit's line number is checked against the
document it points into.

**The `Link` header is the half a consumer actually meets.** A header naming a document that does
not exist is worse than no header, because it is followed. It is also checked on a `HEAD` request:
the middleware that answers `HEAD` passes a **copy** of the scope, so a link added outside it is
silently lost on exactly the requests a consumer uses to read headers cheaply.

## Coverage

| Class | Validates |
|---|---|
| `TestEveryRouteNamesItsDocument` | every mounted route declares a document · the file exists · the index lists it |
| `TestTheLiteralRouteWinsOverTheParameterisedOne` | `/docs/search` reaches the search · a document is still reachable by name, as `text/markdown` |
| `TestTheSearchIndexesOnlyWhatIsServed` | every hit is fetchable · its line exists in that document · the indexed names are the served names |
| `TestTheLinkHeaderPointsSomewhereReal` | present and well-formed · the target answers 200 · survives `HEAD` · absent on an unrouted request |
| `TestTheDeclaredKeysDescribeTheRows` | the document list keys on `name` and the names are unique · a search hit needs its line as well as its document and heading |
| `TestTheRefusals` | empty and blank `q` · an overlong `q` · a `limit` outside its range · an unknown name · a name that tries to leave the served set |
| `TestTheAnswersMatchTheirModels` | both answers parse as their models · an unknown word comes back in `terms_not_matched` · `passages_searched` is stated · a row names the routes it describes |

## The terminal search

The same ranking also runs from a terminal over the whole documentation tree, followed by any
extra search command an installation sets up for itself. Two things can go wrong silently there:
a document in a sub-folder that is never read, and an extra search whose output lands above the
documentation hits or whose missing command ends the search with a traceback.

**Suite:** `tests/framework/api/test_docs_search_console.py`

| Class | Validates |
|---|---|
| `TestTheWholeTreeIsSearchable` | a nested document is found and named by its path below the root · the served set still reads one level · a nested document reads back by that name |
| `TestTheExtraSearches` | each command gets the term as its last argument, after the documentation hits · a command that cannot start is reported, not raised · a fresh clone sets up no extra search |

## Why a hit's key carries its line

The document and the heading alone do **not** separate two hits. A passage longer than the
limit is split into several pieces, and those pieces keep their parent's heading so a reader knows
where they are — which makes the pair repeat where the line never does. The test asserts it over
a deliberately broad query, so the collision is reached rather than assumed.

## What this suite does not cover

The ranking's **quality** — whether the best passage really is the best answer. That is a judgement
and no assertion settles it; what is asserted is that a hit is well-formed, fetchable and points at
a real line. The one behavioural assertion is that a real query returns something at all, which is
what catches a corpus that is not being read.

The documents' **content** is not checked here either, beyond existing. The error vocabulary is the
exception, and it is checked from the other side: `test_api_error_catalog.py` holds the served
error document and the code it describes to one set, in both directions.
