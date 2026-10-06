"""
Documentation API router (#524, #568) — the documents this server carries, and a search over them.

A consumer of this API cannot read this repository. Until these routes existed, every question
about what a field means was answered by hand, in prose, by somebody who could — and the answer
already existed, written down, in a file the asker had no way to open.

Three routes, and their ORDER in this file is load-bearing: `/docs/search` is declared before
`/docs/{name}`, because the parameterised route matches the literal one and would quietly answer
with a document called "search". A test pins the order rather than a comment, since the failure
is silent and a reordering looks harmless.

`/docs/{name}` is this API's only route that does not answer with JSON: a document is markdown and
is served as markdown, because turning prose into a JSON string would only make a consumer undo it.

**Not to be confused with the schema browser at `/docs`.** That one is FastAPI's own, it sits at
the application root rather than under `/api/v1`, and it is switched off as soon as a consumer is
configured. These routes are content, carry no interactivity, and are always on.
"""

from fastapi import APIRouter, Query, Request
from fastapi.responses import PlainTextResponse

from python.api.api_error_catalog import (
    DOCUMENT_NOT_FOUND,
    EMPTY_QUERY,
    INVALID_LIMIT,
    QUERY_TOO_LONG,
    api_error,
)
from python.api.api_route_documents import describes, routes_by_document
from python.framework.docs_search.docs_corpus import DOCS_ROOT
from python.framework.docs_search.docs_search_index import docs_search_index
from python.framework.types.api.docs_types import (
    DocListResponse,
    DocRow,
    DocSearchHit,
    DocSearchResponse,
)

router = APIRouter()

# The most hits one search answers with. A caller asking for more is asking for a reading list
# rather than an answer, and the ranking's tail is where its confidence runs out.
MAX_DOC_HITS = 50

# How long a query may be. The ranking scores every term against every passage, so a pasted
# paragraph costs a multiple of what it can possibly return — and it returns no more than a few
# well-chosen words would.
MAX_QUERY_CHARS = 200

_DEFAULT_HITS = 8


@router.get('/docs', response_model=DocListResponse, openapi_extra=describes('docs'))
def list_documents(request: Request) -> DocListResponse:
    """
    Every document this server carries, with the routes each one describes.

    Args:
        request: The request, for the mounted routes behind the `routes` field

    Returns:
        One row per document, by name
    """
    index = docs_search_index(DOCS_ROOT)
    by_document = routes_by_document(request.app)
    return DocListResponse(documents=[
        DocRow(name=name, title=title, summary=summary, routes=by_document.get(name, []))
        for name, title, summary in index.get_documents()
    ])


@router.get('/docs/search', response_model=DocSearchResponse, openapi_extra=describes('docs'))
def search_documents(
    q: str = Query(..., description='What to search for — a few words, in your own vocabulary'),
    limit: int = Query(_DEFAULT_HITS, description='Maximum hits to return'),
) -> DocSearchResponse:
    """
    The documentation passages that best answer a query, best first.

    An empty `q` is refused rather than answered with everything or with nothing: one character
    is enough to search, so an empty one is the caller's error and not a stage of typing.

    Args:
        q: The query
        limit: Maximum hits to return, at most MAX_DOC_HITS

    Returns:
        The best passages, each naming its document, its heading and the line it starts at
    """
    query = q.strip()
    if not query:
        raise api_error(EMPTY_QUERY)
    if len(query) > MAX_QUERY_CHARS:
        raise api_error(QUERY_TOO_LONG, maximum=MAX_QUERY_CHARS, length=len(query))
    if limit < 1 or limit > MAX_DOC_HITS:
        raise api_error(INVALID_LIMIT, maximum=MAX_DOC_HITS, limit=limit)

    index = docs_search_index(DOCS_ROOT)
    ranked, unmatched = index.search(query, limit)
    return DocSearchResponse(
        query=query,
        passages_searched=index.get_passage_count(),
        hits=[DocSearchHit(document=passage.document, heading=passage.heading,
                           line=passage.line, score=round(score, 3),
                           snippet=index.snippet_for(passage, query))
              for score, passage in ranked],
        terms_not_matched=unmatched,
    )


@router.get('/docs/{name}', response_class=PlainTextResponse, openapi_extra=describes('docs'),
            responses={200: {'content': {'text/markdown': {}},
                             'description': 'The document, as markdown'}})
def get_document(name: str) -> PlainTextResponse:
    """
    One document in full, as markdown.

    Args:
        name: The document's served name, as the index names it

    Returns:
        Its markdown
    """
    text = docs_search_index(DOCS_ROOT).read_document(name)
    if text is None:
        raise api_error(DOCUMENT_NOT_FOUND, name=name)
    return PlainTextResponse(text, media_type='text/markdown; charset=utf-8')
