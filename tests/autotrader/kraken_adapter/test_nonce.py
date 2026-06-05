"""
Kraken Adapter — Nonce Monotonicity & Thread-Safety (#332 follow-up)

The first live Field Study hit `EAPI:Invalid nonce`: a millisecond nonce with no
lock collided under concurrent private calls (worker-thread order-polling +
reconciler). The fix serializes private calls under one lock and makes the nonce
strictly monotone: `max(int(time*1000), last + 1)`. These offline tests pin that
contract (no network — `_sign_request` only signs + stamps the nonce).
"""

import base64
import json
import threading

import pytest

from python.framework.trading_env.adapters.kraken_adapter import KrakenAdapter

_KRAKEN_CONFIG = 'configs/brokers/kraken/kraken_spot_broker_config.json'


@pytest.fixture
def kraken_adapter() -> KrakenAdapter:
    """KrakenAdapter with dummy credentials so _sign_request can HMAC offline."""
    with open(_KRAKEN_CONFIG) as f:
        adapter = KrakenAdapter(json.load(f))
    adapter._api_key = 'test_key'
    adapter._api_secret = base64.b64encode(b'test_secret').decode()
    return adapter


def _next_nonce(adapter: KrakenAdapter) -> int:
    """Generate one signed request and return its stamped nonce as int."""
    data: dict = {}
    adapter._sign_request('/0/private/Test', data)
    return int(data['nonce'])


class TestNonceMonotonicity:
    """The nonce must strictly increase even when the clock does not advance."""

    def test_rapid_calls_strictly_increasing(self, kraken_adapter):
        # 1000 calls land within very few milliseconds → max(now, last+1) makes
        # the counter take over, so every nonce is still strictly greater.
        nonces = [_next_nonce(kraken_adapter) for _ in range(1000)]
        assert all(b > a for a, b in zip(nonces, nonces[1:]))


class TestNonceThreadSafety:
    """Under the private lock, concurrent calls never collide or reorder."""

    def test_concurrent_calls_unique_and_increasing(self, kraken_adapter):
        collected: list = []

        def worker():
            for _ in range(100):
                # Mimic _do_fetch_private: nonce generation happens under the lock.
                with kraken_adapter._private_lock:
                    collected.append(_next_nonce(kraken_adapter))

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        assert len(collected) == 800
        assert len(set(collected)) == 800        # no two calls produced the same nonce
        assert collected == sorted(collected)    # generated in strictly increasing order
