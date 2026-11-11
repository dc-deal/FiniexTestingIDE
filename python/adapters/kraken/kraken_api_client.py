"""
Kraken's REST API, one request at a time: a public GET, or a signed POST for the account's
private reads.

A request that did not complete raises ConnectionAttemptFailedError, classified for the caller's
connection ladder; an error Kraken writes into a successful answer is judged by
`kraken_error_codes` — its documented "not now" is transient, anything else is the venue's answer.
The credentials are read from their file at each signed request and held only for it.

This is the first file of the Kraken package the adapter segregation (#328) plans. The live
adapter and the configuration fetcher still sign their requests with their own copies of the same
arithmetic; they take this client when the segregation moves them here.
"""

import base64
import hashlib
import hmac
import json
import time
import urllib.parse
from typing import Any, Callable, Dict, Optional, Tuple

import requests

from python.configuration.credentials_cascade import (
    resolve_credentials_file,
    tracked_credentials_path,
    workspace_credentials_path,
)
from python.framework.autotrader.kraken_ohlc_bar_fetcher import KrakenOhlcBarFetcher
from python.framework.trading_env.adapters.kraken_error_codes import raise_for_kraken_errors
from python.framework.utils.http_attempt import send_classified


class KrakenApiClient:
    """
    One Kraken REST endpoint per call.

    Args:
        request_timeout_s: Socket timeout of one request
        credentials_file: The configured name of the credentials a signed request uses
            ('venues/kraken_ledger_credentials.json'); None for a client that reads public data only
        api_base_url: Kraken's address; None for the public one every user shares
        get: The GET to send with; requests.get unless a test stands in for the venue
        post: The POST to send with; requests.post likewise
    """

    def __init__(self, request_timeout_s: float, credentials_file: Optional[str] = None,
                 api_base_url: Optional[str] = None,
                 get: Callable[..., requests.Response] = requests.get,
                 post: Callable[..., requests.Response] = requests.post):
        self._request_timeout_s = request_timeout_s
        self._credentials_file = credentials_file
        self._api_base_url = api_base_url or KrakenOhlcBarFetcher.API_BASE
        self._get = get
        self._post = post
        self._last_nonce = 0

    def fetch_public(self, endpoint: str, params: Dict[str, Any]) -> Dict[str, Any]:
        """
        A GET of a public endpoint.

        Args:
            endpoint: The path ('/0/public/Trades')
            params: Its query parameters

        Returns:
            The answer's result
        """
        url = f'{self._api_base_url}{endpoint}'
        response = send_classified(
            lambda: self._get(url, params=params, timeout=self._request_timeout_s), endpoint)
        answer = response.json()
        raise_for_kraken_errors(answer.get('error', []), f'Kraken {endpoint}')
        return answer.get('result', {})

    def fetch_private(self, endpoint: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """
        A signed POST of a private endpoint.

        Args:
            endpoint: The path ('/0/private/Ledgers')
            data: Its form data, without the nonce

        Returns:
            The answer's result
        """
        api_key, api_secret = self._read_credentials()
        payload = dict(data)
        payload['nonce'] = str(self._next_nonce())
        headers = self._signed_headers(endpoint, payload, api_key, api_secret)
        url = f'{self._api_base_url}{endpoint}'
        response = send_classified(
            lambda: self._post(url, headers=headers, data=payload,
                               timeout=self._request_timeout_s),
            endpoint)
        answer = response.json()
        raise_for_kraken_errors(answer.get('error', []), f'Kraken {endpoint}')
        return answer.get('result', {})

    def _next_nonce(self) -> int:
        """
        The next nonce — strictly above the last one, as Kraken requires per key.

        Returns:
            Milliseconds since the epoch, or one above the last nonce where the clock has not
            moved on
        """
        self._last_nonce = max(int(time.time() * 1000), self._last_nonce + 1)
        return self._last_nonce

    def _read_credentials(self) -> Tuple[str, str]:
        """
        The key and secret of the configured credentials, read for one request.

        Returns:
            (api key, api secret)
        """
        if self._credentials_file is None:
            raise ValueError('This Kraken client reads public data only; a private read needs '
                             'a credentials file.')
        path = resolve_credentials_file(self._credentials_file)
        if path is None:
            raise ValueError(
                f'Kraken credentials not found. Expected at one of:\n'
                f'  {workspace_credentials_path(self._credentials_file)} (user override)\n'
                f'  {tracked_credentials_path(self._credentials_file)} (default)')
        document = json.loads(path.read_text(encoding='utf-8'))
        if not document.get('api_key') or not document.get('api_secret'):
            raise ValueError(f'Kraken credentials in {path} are empty — the api_key and the '
                             f'api_secret of a key with read rights are needed.')
        return document['api_key'], document['api_secret']

    @staticmethod
    def _signed_headers(endpoint: str, payload: Dict[str, Any], api_key: str,
                        api_secret: str) -> Dict[str, str]:
        """
        Kraken's signature of a private request: HMAC-SHA512 over the path and the SHA-256 of
        nonce and form data, keyed by the decoded secret.

        Args:
            endpoint: The path
            payload: The form data, nonce included
            api_key: The key
            api_secret: The secret, base64 as Kraken hands it out

        Returns:
            The API-Key and API-Sign headers
        """
        encoded = (payload['nonce'] + urllib.parse.urlencode(payload)).encode()
        message = endpoint.encode() + hashlib.sha256(encoded).digest()
        signature = hmac.new(base64.b64decode(api_secret), message, hashlib.sha512)
        return {'API-Key': api_key, 'API-Sign': base64.b64encode(signature.digest()).decode()}
