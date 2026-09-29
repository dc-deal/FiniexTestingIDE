"""
FiniexTestingIDE - HEAD Request Middleware

HTTP answers HEAD wherever it answers GET (RFC 9110, section 9.3.2): the same status and the same
headers, without the body. FastAPI registers only the methods a route names, so every GET route
answered HEAD with a 405 — and the cheapest way to read the contract header off the API was the
one way that failed.
"""

from starlette.types import ASGIApp, Receive, Scope, Send


class HeadRequestMiddleware:
    """Serve a HEAD request as the GET it asks about; the server then leaves out the body."""

    def __init__(self, app: ASGIApp) -> None:
        """
        Wrap the application.

        Args:
            app: The wrapped ASGI application
        """
        self._app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """
        Pass a HEAD request on as GET, in a COPY of its scope.

        The copy is the point: the server keeps its own scope, which still reads HEAD, and that
        is what makes it send the status and the headers without the body. Rewriting the scope
        in place would make it send the body as well.

        Args:
            scope: The ASGI connection scope
            receive: The ASGI receive channel
            send: The ASGI send channel
        """
        if scope['type'] == 'http' and scope['method'] == 'HEAD':
            scope = dict(scope, method='GET')
        await self._app(scope, receive, send)
