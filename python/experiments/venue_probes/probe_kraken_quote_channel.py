"""Probe: what does Kraken's ticker channel actually deliver beside the trade channel?

The question belongs to #520 step B. Our live tick source subscribes to `ticker` as well as
`trade`, so that every trade tick carries the quote it executed against — the same thing an
archived tick has carried since collector format 1.6.0. Three things that design rests on can
only be answered by the venue, not by a document:

  1. does Kraken accept `event_trigger: "bbo"`, which is what makes the quote track the BOOK
     rather than the trade stream
  2. does the subscription acknowledgement NAME its channel — without that, two subscriptions
     on one connection cannot be confirmed independently and the first ack satisfies both waits
  3. does the taker-side convention hold, i.e. does a BUY print at the ask and a SELL at the bid

Answers 1 and 2 are structural and change only when Kraken deploys; 3 and the timing figures
are distributional and drift with liquidity. That is why this is not a one-shot script — re-run
it and record the answer beside the previous ones.

READ ONLY. Public market data: no credentials, no account, no order. It places nothing and
costs nothing.

It harvests its own subject — it opens the socket and listens — so it needs no argument and no
fixture. The pair and the duration are the only knobs.

Recorded results:
  2026-09-16 12:48 UTC — BTC/USD, 25 s, 240 frames.
      `event_trigger: "bbo"` ACCEPTED, echoed back in the ack.
      Ack names its channel: {"method":"subscribe","result":{"channel":"ticker",
          "event_trigger":"bbo","snapshot":true,"symbol":"BTC/USD"},"success":true}.
      Taker side: 17 of 17 — every BUY printed at the ask, every SELL at the bid, none equal.
      202 ticker frames against 10 trade frames (20:1), median inter-arrival 14 ms, max 2960 ms.
      Spread median 0.10 = 0.00013 %; 0 crossed and 0 equal quotes in 202.
      Quote age at trade time: median 214 ms, max 777 ms.
      Kraken also announced maintenance 2026-09-17 07:01-07:16 UTC in its `status` frame.
  2026-09-16 13:0x UTC — BTC/USD, 40 s, 40 trades, with burst fills separated for the first
      time. Taker side: 14 of 14 on the trades that lead a burst; 26 of 40 trades were INSIDE
      a burst and are excluded, because they measure depth consumed rather than spread paid.
      One sweep walked nine fills from the bid down to -8.9 against a median spread of 0.10.
      This is what the first run's apparent "7 of 12" was: not a broken convention, a large
      market order. Quote age median 392 ms, max 3390 ms; 151 quotes, 0 crossed, 0 equal.

Usage:
    python python/experiments/venue_probes/probe_kraken_quote_channel.py [PAIR] [SECONDS]
"""

import asyncio
import collections
import json
import ssl
import sys
import time
from typing import List, Optional, Tuple

import certifi
import websockets

WS_URL = 'wss://ws.kraken.com/v2'
DEFAULT_PAIR = 'BTC/USD'
DEFAULT_DURATION_S = 25.0


async def collect_frames(pair: str, duration_s: float) -> List[Tuple[float, str]]:
    """
    Open the socket, subscribe to both channels, record every frame with its arrival offset.

    Args:
        pair: Kraken WS v2 pair name (e.g. 'BTC/USD')
        duration_s: How long to listen

    Returns:
        List of (seconds since first frame, raw JSON string)
    """
    ssl_context = ssl.create_default_context(cafile=certifi.where())
    frames: List[Tuple[float, str]] = []

    async with websockets.connect(WS_URL, ssl=ssl_context, ping_interval=20, ping_timeout=10) as ws:
        # Quote channel first, so its snapshot is in hand before trades can arrive — the same
        # order the tick source uses.
        await ws.send(json.dumps({
            'method': 'subscribe',
            'params': {'channel': 'ticker', 'symbol': [pair], 'event_trigger': 'bbo'},
        }))
        await ws.send(json.dumps({
            'method': 'subscribe',
            'params': {'channel': 'trade', 'symbol': [pair]},
        }))

        started = time.monotonic()
        while time.monotonic() - started < duration_s:
            remaining = duration_s - (time.monotonic() - started)
            try:
                message = await asyncio.wait_for(ws.recv(), timeout=remaining)
            except asyncio.TimeoutError:
                break
            frames.append((time.monotonic() - started, message))

    return frames


def report_acks(frames: List[Tuple[float, str]]) -> None:
    """
    Print each subscription acknowledgement verbatim.

    This is question 2, and the raw line is the answer — a paraphrase would hide exactly the
    field being measured.

    Args:
        frames: Recorded frames
    """
    print('=== SUBSCRIPTION ACKS (verbatim) ===')
    for _, raw in frames:
        data = json.loads(raw)
        if data.get('method') == 'subscribe':
            print(raw)
    print()


def report_volume(frames: List[Tuple[float, str]]) -> None:
    """
    Print the frame mix and the ticker channel's inter-arrival spacing.

    Args:
        frames: Recorded frames
    """
    counts = collections.Counter()
    ticker_times: List[float] = []
    for offset, raw in frames:
        data = json.loads(raw)
        key = data.get('channel') or data.get('method') or 'other'
        counts[key] += 1
        if data.get('channel') == 'ticker':
            ticker_times.append(offset)

    print('=== FRAME MIX ===')
    for key, count in counts.most_common():
        print(f'  {key}: {count}')

    if len(ticker_times) > 1:
        gaps = sorted(
            round((ticker_times[i + 1] - ticker_times[i]) * 1000)
            for i in range(len(ticker_times) - 1)
        )
        print(f'  ticker inter-arrival ms: median={gaps[len(gaps) // 2]} max={gaps[-1]}')
    print()


def report_quotes_and_trades(frames: List[Tuple[float, str]]) -> None:
    """
    Replay the frames the way the parser does and check the taker-side convention.

    Walks the stream in order, keeping the last quote, and states for every trade whether it
    printed at the ask (BUY) or the bid (SELL) — question 3, and the one that decides whether a
    spread can be reconstructed for archived data that has none.

    Args:
        frames: Recorded frames
    """
    quote: Optional[Tuple[float, float, float]] = None
    spreads: List[float] = []
    ages: List[float] = []
    crossed = 0
    equal = 0
    matches = 0
    checked = 0
    trades = 0
    no_quote = 0
    in_burst = 0
    last_trade_stamp = ''

    print('=== TRADES AGAINST THE QUOTE THEY EXECUTED ON ===')
    print(f'{"side":5} {"price":>12} {"bid":>12} {"ask":>12} {"age_ms":>7}  convention')

    for offset, raw in frames:
        data = json.loads(raw)
        channel = data.get('channel')
        if data.get('type') not in ('snapshot', 'update'):
            continue

        if channel == 'ticker':
            for entry in data.get('data', []):
                bid = float(entry.get('bid', 0))
                ask = float(entry.get('ask', 0))
                if ask < bid:
                    crossed += 1
                    continue
                if ask == bid:
                    equal += 1
                spreads.append(ask - bid)
                quote = (bid, ask, offset * 1000)

        elif channel == 'trade':
            for entry in data.get('data', []):
                trades += 1
                price = float(entry.get('price', 0))
                side = entry.get('side', '').upper()
                if quote is None:
                    no_quote += 1
                    print(f'{side:5} {price:12.5f} {"—":>12} {"—":>12} {"—":>7}  no quote yet')
                    continue
                bid, ask, observed_ms = quote
                age_ms = offset * 1000 - observed_ms
                ages.append(age_ms)

                # A BURST is several fills sharing one exchange timestamp: one market order
                # sweeping through deeper book levels. Only its FIRST fill pays the spread the
                # quote describes; the rest measure DEPTH CONSUMED, which is a different
                # quantity under the same name. Judging them against the top of book would
                # report the convention as broken when what actually happened is a large order.
                stamp = entry.get('timestamp', '')
                burst = stamp != '' and stamp == last_trade_stamp
                last_trade_stamp = stamp

                expected = ask if side == 'BUY' else bid
                if burst:
                    in_burst += 1
                    verdict = f'burst fill, {price - expected:+.5g} through the book'
                else:
                    checked += 1
                    ok = price == expected
                    matches += 1 if ok else 0
                    verdict = 'ok' if ok else f'MISMATCH (expected {expected})'
                print(
                    f'{side:5} {price:12.5f} {bid:12.5f} {ask:12.5f} {age_ms:7.0f}  {verdict}'
                )

    print()
    print('=== SUMMARY ===')
    if spreads:
        ordered = sorted(spreads)
        median = ordered[len(ordered) // 2]
        reference = quote[0] if quote else 0.0
        relative = f'{median / reference * 100:.5f} %' if reference > 0 else 'n/a'
        print(f'  quotes: {len(spreads)}  crossed: {crossed}  bid==ask: {equal}')
        print(f'  spread min={ordered[0]:.5g} median={median:.5g} max={ordered[-1]:.5g}'
              f'  (median relative {relative})')
    print(f'  trades: {trades}  without a quote: {no_quote}  inside a burst: {in_burst}')
    if checked:
        print(f'  taker-side convention held: {matches} of {checked}'
              f'  (burst fills excluded — they consume depth, not spread)')
    if ages:
        ordered_ages = sorted(ages)
        print(f'  quote age at trade time: median {ordered_ages[len(ordered_ages) // 2]:.0f} ms'
              f'  max {ordered_ages[-1]:.0f} ms')


def main() -> None:
    """Run the probe and print all three answers."""
    pair = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_PAIR
    duration_s = float(sys.argv[2]) if len(sys.argv) > 2 else DEFAULT_DURATION_S

    print(f'Probing {WS_URL} — {pair}, {duration_s:.0f}s, read only\n')
    frames = asyncio.run(collect_frames(pair, duration_s))
    print(f'{len(frames)} frames recorded\n')

    report_acks(frames)
    report_volume(frames)
    report_quotes_and_trades(frames)


main()
