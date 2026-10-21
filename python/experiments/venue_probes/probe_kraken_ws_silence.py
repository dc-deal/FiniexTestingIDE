"""Probe: how long does a healthy Kraken WebSocket v2 connection stay silent?

The question belongs to #609. The live tick source declares a connection dead after
`tick_source.connection_dead_s` without a message and reconnects. Every frame counts as a sign
of life — trades, ticker updates, status frames and the venue's heartbeat — so the threshold may
be no shorter than the longest pause a HEALTHY connection shows. Kraken's documentation says a
heartbeat is sent about once a second when no other update is due; this measures what the venue
actually does.

A session opens one connection, subscribed to one pair: `trade`, and by default `ticker` with
`event_trigger: "bbo"` before it (`tick_source.quote_channel_enabled`). The probe opens both shapes
for every symbol of the Kraken seed configuration at once and records, per connection, the longest
pause between two frames and how often a heartbeat arrived. The heartbeat is a property of the
connection, not of the pair, so a quiet pair on its own connection is the case that matters.

READ ONLY. Public market data: no credentials, no account, no order. It places nothing and costs
nothing. It opens two connections per seed symbol at the same moment — eighteen for the seed of
2026-10-20 — far below the roughly 150 connection attempts per address in ten minutes that #609
cites for the venue's front.

It harvests its own subjects from the seed configuration, so it needs no argument; the listening
time is the only knob. Run it from the repository root.

Recorded results:
  2026-10-20 12:45 UTC — the nine seed pairs x both shapes, 180 s, 18 connections, none failed.
      Heartbeats arrive about once a second on EVERY connection, also while ticker and trade
      updates flow — not only in their absence, as the documentation reads: 179 or 180 per
      connection, median spacing 1.00 s, the widest 1.65 s (ETH/EUR, trade+ticker).
      Longest pause between two frames on a healthy connection: 1.03 s with the ticker channel,
      1.15 s trades only (BTC/USD); no pause above 2 s on any connection.
      → a silence threshold of at least 4 s; #609 sets 10 s, the data collector's value.

Usage:
    python python/experiments/venue_probes/probe_kraken_ws_silence.py [SECONDS]
"""

import asyncio
import json
import math
import ssl
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List

import certifi
import websockets

WS_URL = 'wss://ws.kraken.com/v2'
SEED_PATH = Path('configs/brokers/kraken/kraken_spot_broker_config.json')
DEFAULT_DURATION_S = 180.0
# The two subscription shapes a session can open: the default, and the quote channel switched off.
SHAPE_WITH_QUOTES = 'trade+ticker'
SHAPE_TRADES_ONLY = 'trade'
# The thresholds the report counts pauses against, in seconds.
REPORTED_PAUSES_S = (2.0, 5.0, 10.0)
# How far the silence threshold stays above the longest healthy pause measured.
THRESHOLD_FACTOR = 3.0


@dataclass
class ConnectionPauses:
    """What one connection showed: its frames by channel and the instants they arrived."""
    pair: str
    shape: str
    subscribed_at: float = 0.0
    ended_at: float = 0.0
    frames_by_channel: Dict[str, int] = field(default_factory=dict)
    arrivals: List[float] = field(default_factory=list)
    heartbeat_arrivals: List[float] = field(default_factory=list)
    error: str = ''

    def pauses(self) -> List[float]:
        """
        Every pause the silence check would have seen, the open one at the end included.

        Returns:
            Seconds between consecutive frames, starting at the subscription
        """
        instants = [self.subscribed_at] + self.arrivals + [self.ended_at]
        return [later - earlier for earlier, later in zip(instants, instants[1:])]

    def heartbeat_intervals(self) -> List[float]:
        """
        The spacing of the venue's heartbeats on this connection.

        Returns:
            Seconds between consecutive heartbeats
        """
        beats = self.heartbeat_arrivals
        return [later - earlier for earlier, later in zip(beats, beats[1:])]


def seed_pairs() -> List[str]:
    """
    The WebSocket pairs of every symbol in the Kraken seed configuration.

    Returns:
        Pairs such as 'ETH/USD', in the seed's order
    """
    seed = json.loads(SEED_PATH.read_text(encoding='utf-8'))
    return [f"{spec['base_currency']}/{spec['quote_currency']}"
            for spec in seed['symbols'].values()]


async def listen(pair: str, shape: str, duration_s: float,
                 ssl_context: ssl.SSLContext) -> ConnectionPauses:
    """
    Open one connection the way the tick source does and record when every frame arrives.

    Args:
        pair: Kraken WS v2 pair (e.g. 'ETH/USD')
        shape: SHAPE_WITH_QUOTES or SHAPE_TRADES_ONLY
        duration_s: How long to listen after subscribing
        ssl_context: Verified TLS context

    Returns:
        The connection's frames and arrival instants, on the monotonic clock
    """
    result = ConnectionPauses(pair=pair, shape=shape)
    try:
        async with websockets.connect(
                WS_URL, ssl=ssl_context, ping_interval=20, ping_timeout=10) as ws:
            if shape == SHAPE_WITH_QUOTES:
                await ws.send(json.dumps({'method': 'subscribe', 'params': {
                    'channel': 'ticker', 'symbol': [pair], 'event_trigger': 'bbo'}}))
            # No `snapshot` parameter, as the tick source sends it.
            await ws.send(json.dumps({'method': 'subscribe', 'params': {
                'channel': 'trade', 'symbol': [pair]}}))
            result.subscribed_at = time.monotonic()
            deadline = result.subscribed_at + duration_s
            while True:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    break
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
                except asyncio.TimeoutError:
                    break
                arrived = time.monotonic()
                frame = json.loads(raw)
                channel = frame.get('channel') or frame.get('method') or 'unknown'
                result.frames_by_channel[channel] = result.frames_by_channel.get(channel, 0) + 1
                result.arrivals.append(arrived)
                if channel == 'heartbeat':
                    result.heartbeat_arrivals.append(arrived)
    except Exception as error:
        # One refused connection must not cost the other seventeen their answer.
        result.error = f'{type(error).__name__}: {error}'
    result.ended_at = time.monotonic()
    return result


async def listen_to_all(pairs: List[str], duration_s: float) -> List[ConnectionPauses]:
    """
    Listen on both shapes for every pair at the same time.

    Args:
        pairs: Kraken WS v2 pairs
        duration_s: How long each connection listens

    Returns:
        One result per pair and shape
    """
    ssl_context = ssl.create_default_context(cafile=certifi.where())
    return list(await asyncio.gather(*[
        listen(pair, shape, duration_s, ssl_context)
        for pair in pairs for shape in (SHAPE_WITH_QUOTES, SHAPE_TRADES_ONLY)]))


def report(results: List[ConnectionPauses]) -> None:
    """
    Print one line per connection, then the longest healthy pause per shape.

    Args:
        results: What every connection showed
    """
    header = ('pair        shape          frames  heartbeats  beat spacing (median / max)'
              '  longest pause')
    print(header + ''.join(f'  >{limit:.0f}s' for limit in REPORTED_PAUSES_S))
    for result in results:
        if result.error:
            print(f'{result.pair:<11} {result.shape:<14} FAILED — {result.error}')
            continue
        pauses = result.pauses()
        spacing = sorted(result.heartbeat_intervals())
        spacing_text = (f'{spacing[len(spacing) // 2]:.2f} / {spacing[-1]:.2f} s'
                        if spacing else '—')
        counts = ''.join(f'  {sum(1 for pause in pauses if pause > limit):>4}'
                         for limit in REPORTED_PAUSES_S)
        print(f'{result.pair:<11} {result.shape:<14} {len(result.arrivals):>6}  '
              f'{len(result.heartbeat_arrivals):>10}  {spacing_text:<27}  '
              f'{max(pauses):>10.2f} s{counts}')
    print()
    for shape in (SHAPE_WITH_QUOTES, SHAPE_TRADES_ONLY):
        healthy = [result for result in results if result.shape == shape and not result.error]
        if not healthy:
            continue
        longest = max(max(result.pauses()) for result in healthy)
        print(f'{shape}: longest pause on a healthy connection {longest:.2f} s → a silence '
              f'threshold of at least {math.ceil(longest * THRESHOLD_FACTOR)} s '
              f'({THRESHOLD_FACTOR:.0f}x)')


def main() -> None:
    """Run the probe over the seed's pairs and print the answer."""
    duration_s = float(sys.argv[1]) if len(sys.argv) > 1 else DEFAULT_DURATION_S
    pairs = seed_pairs()
    print(f'Probing {WS_URL} — {len(pairs)} pairs x 2 shapes, {duration_s:.0f}s, read only\n')
    report(asyncio.run(listen_to_all(pairs, duration_s)))


if __name__ == '__main__':
    main()
