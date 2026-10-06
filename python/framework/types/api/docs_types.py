"""
Documentation API types (#524, #568) — what the served documents say about themselves.

A consumer of this API cannot read this repository, so every explanation of what a field means had
to be delivered by hand. Two answers replace that: an index of the documents the server carries,
and a ranked search over their passages.

The documents themselves are markdown and are served as markdown — only what a caller needs in
order to CHOOSE one is modelled here.
"""

from typing import List

from pydantic import BaseModel

# What makes one document row unique (§49): its served NAME, which is what `/docs/{name}` takes.
# The title is not a key — two documents may legitimately carry the same heading, and a title is
# written for a reader while the name is written for a caller.
DOCUMENT_ROW_KEY = ['name']

# What makes one search hit unique (§49): the document, the heading within it, AND the line. The
# first two alone are not enough — a long passage is split into pieces that keep their parent's
# heading, so the pair repeats where the line never does.
SEARCH_HIT_KEY = ['document', 'heading', 'line']


class DocRow(BaseModel):
    """
    One served document, as far as choosing between them needs.

    Args:
        name: Its served name — what `/api/v1/docs/{name}` takes
        title: Its own heading, for a reader
        summary: Its opening paragraph, taken FROM the document rather than declared beside it,
            so the sentence in this list and the sentence at the top of the document cannot drift
            apart
        routes: The routes that declare this document as their description — the reverse of the
            `Link` header each of those routes answers with, so the pair can be navigated from
            either end
    """
    name: str
    title: str
    summary: str
    routes: List[str] = []


class DocListResponse(BaseModel):
    """
    Every document this server carries.

    Args:
        key: What makes one row unique
        documents: The documents, by name
    """
    key: List[str] = DOCUMENT_ROW_KEY
    documents: List[DocRow]


class DocSearchHit(BaseModel):
    """
    One passage that answers a query.

    A passage rather than a document, because a document is more than the answer: the heading says
    what the hit IS before it is fetched, and the line is where to start reading in the markdown
    the document route serves.

    Args:
        document: Which document holds it — fetch it from `/api/v1/docs/{document}`
        heading: The heading it stands under
        line: Which line of that document the passage starts at, 1-based
        score: How well it matched, on the ranking's own scale — comparable WITHIN one answer and
            meaningless between two, since it depends on how rare the query's words are
        snippet: One line from around the first matching term, so a caller can recognise the
            answer without fetching the document
    """
    document: str
    heading: str
    line: int
    score: float
    snippet: str


class DocSearchResponse(BaseModel):
    """
    The passages that best answer a query, best first.

    Args:
        key: What makes one hit unique
        query: What was searched for, echoed so a saved answer says what produced it
        passages_searched: How many passages the query was scored against — the size of the corpus
            behind this answer, which is what makes an empty `hits` readable
        hits: The best passages, best first; empty when nothing matched
        terms_not_matched: The query's words that appear in NO served document. This is what tells
            a caller to try another word instead of leaving them to guess why an answer looks
            wrong — a word that is simply not our vocabulary contributes nothing to the ranking,
            and from the outside that is invisible
    """
    key: List[str] = SEARCH_HIT_KEY
    query: str
    passages_searched: int
    hits: List[DocSearchHit]
    terms_not_matched: List[str] = []
