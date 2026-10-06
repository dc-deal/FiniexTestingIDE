"""
Every answer says where it is explained (#524).

A consumer reading a field they do not understand has nowhere to go: the explanation exists, in
this repository, which they cannot open. So each answer carries a `Link` header naming the
document that describes the route — `rel="describedby"`, which is a registered relation meaning
exactly that, so a consumer following it needs no agreement with us beyond the specification.

It is a HEADER rather than a field for two reasons. A body that is a bare array — the bar routes —
has nowhere to put it. And a header is additive: a client that ignores it is unaffected, which is
what lets this reach every route at once instead of one response model at a time.

**It must sit INSIDE the HEAD middleware.** That one passes a HEAD request on in a COPY of its
scope, so the router's match lands on the copy; a middleware wrapped around it would read the
original and find no route. Since the outermost middleware is the one added last, this one is
added FIRST.
"""

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from python.api.api_route_documents import document_link


class LinkHeaderMiddleware:
    """Put the describing document's link on every answer whose route names one."""

    def __init__(self, app: ASGIApp) -> None:
        """
        Wrap the application.

        Args:
            app: The wrapped ASGI application
        """
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """
        Answer as the wrapped application does, with the document link added.

        The route is read when the response STARTS, not before: routing happens inside this
        middleware, so the matched route reaches the scope only once the handler answers. A
        request that matched nothing — a 404, a refusal before routing — carries no link, which
        is correct: there is no route to describe.

        Args:
            scope: The ASGI connection scope
            receive: The ASGI receive channel
            send: The ASGI send channel
        """
        if scope['type'] != 'http':
            await self._app(scope, receive, send)
            return

        async def send_with_link(message: Message) -> None:
            if message['type'] == 'http.response.start':
                link = document_link(scope.get('route'))
                if link is not None:
                    MutableHeaders(scope=message).append('Link', link)
            await send(message)

        await self._app(scope, receive, send_with_link)
