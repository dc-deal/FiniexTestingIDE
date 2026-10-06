# Reading the documentation from the API

You are holding a response and do not know what one of its fields means. The explanation exists
and is written down — in a repository you may not have. Over three conversations on 2026-09-17
the meaning of a data origin, the gap categories and the difference between coverage and gaps
were each explained by hand, in prose, to somebody who had no way to look them up. These routes
replace that: the same documents, served by the same server that serves the data.

Every route also names its own document. The answer carries a header:

```
Link: </api/v1/docs/booking-periods>; rel="describedby"
```

`describedby` is a registered relation meaning exactly that — the resource that describes this
one — so following it needs no agreement with us beyond the HTTP specification.

**What is not here:** anything about how this project is built. These documents describe what
the API answers with. They are written for somebody integrating against it, not for somebody
changing it.

## The three routes

| Route | Answers with |
|---|---|
| `GET /api/v1/docs` | every document this server carries, with the routes each one describes |
| `GET /api/v1/docs/search?q=…` | the passages that best answer a question, best first |
| `GET /api/v1/docs/{name}` | one document in full, as `text/markdown` |

The document route is this API's only answer that is not JSON. A document is markdown and is
served as markdown — wrapping prose in a JSON string would only make you unwrap it.

## Search, when you do not know the document's name

You know what you want to understand; you do not know what the document is called. The search
answers in **passages** rather than documents — a passage being the text under one heading — so a
hit names the heading that carries the answer and the line it starts at:

```json
{ "document": "gaps", "heading": "Why a weekend is not a gap in the data",
  "line": 34, "score": 9.214, "snippet": "a weekend is `weekend` on forex and never appears …" }
```

Fetch `/api/v1/docs/gaps` and start reading at that line.

`terms_not_matched` lists the words of your query that appear in **no** document. That is the
field to read when an answer looks wrong: a word that is not our vocabulary contributes nothing
to the ranking, and from the outside that is invisible. If `origin` matched and `provenance` did
not, the documents call it something else.

`passages_searched` says how large the corpus behind the answer was, which is what makes an empty
`hits` readable — nothing matched, rather than nothing was searched.

A *passage* is deliberately not a *section*: in this API a section is one section of a run report,
and one word carrying two contracts is how a reader comes to hold the wrong one.

## What the ranking does and does not promise

It is Okapi BM25 over the passages, which weighs a rare word far above a common one. That is what
makes a query of ordinary words land on the one passage using an unusual one — and it is also why
a good hit often does **not** contain every word you typed. Do not read a missing word
as a bad match.

`score` is comparable within one answer and meaningless between two: it depends on how rare your
words are, so the best hit for one query may score three times the best hit for another without
being three times better.

## Errors

An empty `q` is refused with `400 empty_query` rather than answered with everything or with
nothing. One character is enough to search, so an empty query is an error and not a stage of
typing. A query longer than the cap is `400 query_too_long`; a name no document goes by is
`404 document_not_found`. The full vocabulary is in [errors](/api/v1/docs/errors).
