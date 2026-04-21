"""
FiniexTestingIDE - API Server CLI

Starts the FiniexTestingIDE HTTP API server via uvicorn.

Usage:
    python python/cli/api_server_cli.py
    python python/cli/api_server_cli.py --host 0.0.0.0 --port 8000 --reload
"""

import argparse

import uvicorn


class ApiServerCli:
    """
    Command-line interface for starting the FiniexTestingIDE API server.

    Args:
        host: Bind address for the server.
        port: Port to listen on.
        reload: Enable auto-reload on source changes (development only).
    """

    def __init__(self, host: str, port: int, reload: bool):
        self._host = host
        self._port = port
        self._reload = reload

    def run(self) -> None:
        """Start the uvicorn server with the configured parameters."""
        uvicorn.run(
            'python.api.api_app:create_app',
            host=self._host,
            port=self._port,
            reload=self._reload,
            factory=True,
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description='FiniexTestingIDE API Server',
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument('--host', default='0.0.0.0', help='Bind address')
    parser.add_argument('--port', type=int, default=8000, help='Port')
    parser.add_argument('--reload', action='store_true', help='Enable auto-reload (dev)')
    args = parser.parse_args()

    ApiServerCli(host=args.host, port=args.port, reload=args.reload).run()


if __name__ == '__main__':
    main()
