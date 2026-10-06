"""
Which document describes which route (#524).

A consumer of this API cannot read this repository, so a route that answers with fields they do
not understand leaves them nowhere to go. Every route therefore names the document that explains
it, and the answer carries that name as a `Link` header — the HTTP way of saying "what this means
is over there".

**The declaration rides on the route itself**, as an OpenAPI extension in its decorator, rather
than in a table beside it. A table is a second place to forget: a route added without an entry
looks exactly like one that needs none. Here the declaration sits where the route is written, it
travels into the published schema so a consumer can read the whole mapping at once, and the test
that walks every mounted route finds the omission.

Reading it back takes two different paths, for a reason worth knowing before changing either.
At REQUEST time the route object is on the scope and carries its own extras, which costs nothing.
A WALK over everything reads the generated schema instead, because it is the only enumeration
that carries each route's FULL path. This FastAPI includes a router as one lazy placeholder, so
`app.routes` holds just the routes the factory mounts itself — six of the thirty-eight, measured
2026-10-06. Descending into the placeholder is possible (it keeps the real router on
`original_router`, which is how the row-key gate walks the app), but the routes found that way
carry their path WITHOUT the mount prefix, and the prefix is half of what a consumer is told.
"""

from typing import Dict, List, Optional, Tuple

from fastapi import FastAPI

from python.api.api_contract import API_PREFIX

# An OpenAPI extension key, which the specification requires to begin with `x-`. Named for this
# project so it cannot collide with a convention a tool introduces later.
DOC_KEY = 'x-finiex-doc'

# The relation the `Link` header declares. Registered in RFC 8288's relation registry and meaning
# exactly this: a resource that describes the one being answered. A consumer following it needs
# no agreement with us beyond the specification.
LINK_RELATION = 'describedby'


def describes(name: str) -> Dict[str, str]:
    """
    The `openapi_extra` that names a route's document.

    Args:
        name: The document's served name, as `/api/v1/docs/{name}` takes it

    Returns:
        The extension to pass as the route's `openapi_extra`
    """
    return {DOC_KEY: name}


def declared_document(route: object) -> Optional[str]:
    """
    The document a single route declares, read from the route object itself.

    This is the request-time path: the route is on the request scope, so nothing has to be
    generated to answer.

    Args:
        route: The matched route, or anything that is not one

    Returns:
        The document's name, or None when the route declares none or is not an API route
    """
    extra = getattr(route, 'openapi_extra', None)
    if not isinstance(extra, dict):
        return None
    name = extra.get(DOC_KEY)
    return name if isinstance(name, str) else None


def document_link(route: object) -> Optional[str]:
    """
    The `Link` header value naming a route's document.

    An absolute path reference rather than a relative one: a relative reference resolves against
    the request's own URL, which on a route several segments deep is a different answer per route
    — and the consumer would have to do that resolution to find out they all meant the same file.

    Args:
        route: The matched route, or anything that is not one

    Returns:
        The header value, or None when the route declares no document
    """
    name = declared_document(route)
    if name is None:
        return None
    return f'<{API_PREFIX}/docs/{name}>; rel="{LINK_RELATION}"'


def route_documents(app: FastAPI) -> List[Tuple[str, str, Optional[str]]]:
    """
    Every mounted route and the document it declares.

    Read from the generated schema, which is the only enumeration that descends into the included
    routers. FastAPI caches that schema after the first call, so a repeated walk costs nothing.

    Args:
        app: The fully mounted application

    Returns:
        One (path, method, document or None) per route and method, in path order
    """
    walked: List[Tuple[str, str, Optional[str]]] = []
    for path, operations in sorted(app.openapi()['paths'].items()):
        for method, operation in operations.items():
            name = operation.get(DOC_KEY)
            walked.append((path, method.upper(), name if isinstance(name, str) else None))
    return walked


def routes_by_document(app: FastAPI) -> Dict[str, List[str]]:
    """
    Which routes each document describes — the reverse of the `Link` header.

    Args:
        app: The fully mounted application

    Returns:
        Document name → the paths that declare it, each path once and in order
    """
    mapping: Dict[str, List[str]] = {}
    for path, _method, name in route_documents(app):
        if name is None:
            continue
        paths = mapping.setdefault(name, [])
        if path not in paths:
            paths.append(path)
    return mapping
