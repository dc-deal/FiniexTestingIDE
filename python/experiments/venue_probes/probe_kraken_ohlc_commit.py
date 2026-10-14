"""Probe: when does Kraken's OHLC answer commit a period that has just ended, and how does it look?

The question arose in #588 (the warmup history); #328 owns the probe from there on. It moves the
Kraken code into a package of its own, which this probe and the recording it writes follow, and
the release gate re-records with it as long as the gate checks the answer's shape. A live session
reads its warmup from
`GET /0/public/OHLC` and takes a row only when two things hold: the period has ended by the instant
of the read, and the venue has committed it — the answer's `last` field names the newest committed
period, and the row after it is the one still forming. The seam at the first tick depends on how
soon after a boundary that commit happens, and the warmup suite's recorded fixture depends on what
the answer looks like either side of it. Both are the venue's to answer and both can change when it
deploys, which is why this is re-runnable rather than a one-shot script.

READ ONLY. Public market data: no credentials, no account, no order. It places nothing and costs
nothing. It harvests its own subject: it waits for the next minute boundary and polls around it.

With `--record PATH` it also writes the two answers either side of the commit — verbatim, the
request's narrow `since` keeping them short — to PATH. The warmup suite reads them from
`tests/fixtures/warmup_history/kraken_ohlc_answers.json`, and the live adapter release gate checks
that the venue still answers in their shape; when that check goes red, re-record here.

Recorded results:
  2026-10-12 19:48 UTC — ETHUSD, M1, polled every 0.5 s from 4 s before to 8 s after the boundary.
      +0.39 s: `last` still named the period before the one that had just ended, and the newest
      row was that period (27 trades). +0.79 … 0.91 s: `last` named the period that had ended, and
      a fresh row for the new minute appeared with 0 trades. Commit lag about 0.8 s.
  2026-10-13 07:30 UTC — ETHUSD, M1, the same polling, recorded into the fixture. `last` still
      named the period before the ended minute at +1.50 s, whose row was the newest (16 trades);
      at +2.18 s it named the ended minute, and the fresh row for the new one already held 1 trade.
      Commit lag between 1.5 and 2.2 s — more than twice the day before, so the lag is not a
      constant. The answer's result key is `XETHZUSD`, not the requested `ETHUSD`.

Usage:
    python python/experiments/venue_probes/probe_kraken_ohlc_commit.py [PAIR] [--record PATH]
"""

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import requests

OHLC_URL = 'https://api.kraken.com/0/public/OHLC'
INTERVAL_MINUTES = 1
POLL_EVERY_S = 0.5
BEFORE_S = 4.0
AFTER_S = 8.0
ROWS_BACK = 20


def _poll_around_next_boundary(pair: str) -> Dict:
    """
    Wait for the next minute boundary and read the answer every half second around it.

    Args:
        pair: Venue pair name, e.g. 'ETHUSD'

    Returns:
        The boundary (unix seconds) and every answer with its send and receive offsets
    """
    now = time.time()
    boundary = (int(now // 60) + 1) * 60
    if boundary - now < BEFORE_S + 1:
        boundary += 60
    time.sleep(max(0.0, boundary - BEFORE_S - time.time()))

    polls: List[Dict] = []
    while time.time() < boundary + AFTER_S:
        sent = time.time()
        response = requests.get(OHLC_URL, params={
            'pair': pair, 'interval': INTERVAL_MINUTES,
            'since': boundary - ROWS_BACK * 60}, timeout=10)
        received = time.time()
        polls.append({'sent': sent, 'received': received, 'answer': response.json()})
        time.sleep(POLL_EVERY_S)
    return {'boundary': boundary, 'polls': polls}


def _rows_of(result: Dict) -> List[list]:
    """
    The OHLC rows of one answer's result — the one list-valued key beside `last`.

    Args:
        result: The answer's `result` object

    Returns:
        The rows, oldest first
    """
    return next(value for key, value in result.items() if key != 'last')


def _either_side_of_the_commit(boundary: int, polls: List[Dict]) -> Dict[str, Optional[Dict]]:
    """
    The last answer received after the boundary that had not committed the ended minute yet, and
    the first that had.

    Args:
        boundary: The minute boundary, unix seconds
        polls: Every answer of the run

    Returns:
        {'before_commit': poll or None, 'after_commit': poll or None}
    """
    ended = boundary - 60
    before, after = None, None
    for poll in polls:
        committed = poll['answer']['result']['last']
        if committed < ended and poll['received'] >= boundary:
            before = poll
        if committed >= ended and after is None:
            after = poll
    return {'before_commit': before, 'after_commit': after}


def _print(boundary: int, polls: List[Dict]) -> None:
    """
    One line per answer: when it was asked, what `last` named, what the newest row was.

    Args:
        boundary: The minute boundary, unix seconds
        polls: Every answer of the run

    Returns:
        None
    """
    print(f'boundary {datetime.fromtimestamp(boundary, tz=timezone.utc).isoformat()}')
    print('  sent    recv   last  newest row  trades in it')
    for poll in polls:
        result = poll['answer']['result']
        newest = _rows_of(result)[-1]
        print(f"  {poll['sent'] - boundary:+5.2f} {poll['received'] - boundary:+6.2f}"
              f"  {result['last'] - boundary:+5d}  {newest[0] - boundary:+5d}       {newest[7]}")


def _record(path: Path, pair: str, boundary: int, sides: Dict[str, Optional[Dict]]) -> None:
    """
    Write the two answers either side of the commit, verbatim, with when they arrived.

    Args:
        path: The fixture file to write
        pair: Venue pair name
        boundary: The minute boundary, unix seconds
        sides: The answers from _either_side_of_the_commit

    Returns:
        None
    """
    missing = [side for side, poll in sides.items() if poll is None]
    if missing:
        raise SystemExit(f'Not recorded — no answer {missing} in this run; run the probe again.')
    fixture = {
        '_comment': (
            'Real Kraken OHLC answers (GET /0/public/OHLC), verbatim, either side of the moment '
            'the venue committed a just-ended minute. Recorded by '
            'python/experiments/venue_probes/probe_kraken_ohlc_commit.py (#588); the live adapter '
            'release gate checks that the venue still answers in this shape.'),
        'pair': pair,
        'interval_minutes': INTERVAL_MINUTES,
        'boundary': datetime.fromtimestamp(boundary, tz=timezone.utc).isoformat(),
        **{
            side: {
                'received_at': datetime.fromtimestamp(
                    poll['received'], tz=timezone.utc).isoformat(),
                'answer': poll['answer'],
            }
            for side, poll in sides.items()
        },
    }
    path.write_text(json.dumps(fixture, indent=1) + '\n')
    print(f'recorded → {path}')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('pair', nargs='?', default='ETHUSD')
    parser.add_argument('--record', type=Path, default=None,
                        help='Write the answers either side of the commit to this file')
    args = parser.parse_args()

    run = _poll_around_next_boundary(args.pair)
    _print(run['boundary'], run['polls'])
    sides = _either_side_of_the_commit(run['boundary'], run['polls'])
    for side, poll in sides.items():
        offset = f"{poll['received'] - run['boundary']:+.2f} s" if poll else 'none in this run'
        print(f'{side}: {offset}')
    if args.record:
        _record(args.record, args.pair, run['boundary'], sides)


if __name__ == '__main__':
    main()
