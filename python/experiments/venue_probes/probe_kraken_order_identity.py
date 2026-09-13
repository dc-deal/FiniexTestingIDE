"""Probe: does Kraken answer about an order by OUR key, and what does it say when it is gone?

The questions belong to #487. Its resolution path asks the venue what became of a write whose
answer was lost — and the whole design rests on four facts the documentation does not settle:

  Q1  does QueryOrders accept `cl_ord_id`, our own key, or only Kraken's txid?
  Q2  how far back does a reference stay answerable? (the retention EDGE, extending the series
      the txid-retention probe started)
  Q3  is `cl_ord_id` uniqueness scoped to OPEN orders, or to all history?
  Q4  what does CancelOrder answer for an order that is already gone?

Q3 is already ANSWERED by the archive and costs nothing here — see the ARCHIVE section below.
Q4's answer for a FILLED order is likewise already on record. What is left is narrow, and that
is the point: a probe should spend calls only on what is still open.

WHAT IT COSTS
    `archive` mode: nothing at all. No network, no credential touched.
    `read` mode:    private READS only. Places nothing, cancels nothing, amends nothing.
    `full` mode:    adds CancelOrder calls against references that are already terminal. They
                    CREATE nothing — there is no order to leave behind — but CancelOrder is a
                    write route, so it is opt-in and runs last.
    No fee is charged in any mode.

    It reaches the adapter's Tier-3 transport (`_fetch_private`) on purpose: the point is what
    the VENUE sends, and the parse layer flattens exactly the detail being measured.
    `parse_query_response` is called beside it to show what production makes of the same answer.

ARCHIVE — what is already measured, and needs no call
    Q3 is refuted as an open question: every CLOSE reuses its entry's counter, so a key is sent
    at least twice per position by construction, and the archive is full of accepted re-uses.
    The one recorded refusal (`EGeneral:Invalid arguments:cl_ord_id not unique`, 2026-09-10)
    had its colliding sibling STILL RESTING at that moment, which is consistent with open-order
    scoping and says nothing about history.
    Q4 for a FILLED order: `EOrder:Unknown order` — an ERROR, not a clean `{'count': 0}`
    (2026-09-08, two occurrences on /0/private/CancelOrder).

MEASUREMENTS
    (append each run here, dated, newest last — a repeated measurement is a series, not a
     series of surprises)

    2026-09-12 18:50 UTC — 70 references across 6 runs, account flat (0 resting).

      Q1  Kraken ACCEPTS `cl_ord_id` on QueryOrders. The negative control is what proves it:
          txid + the order's real key answers with the order, txid + a key that was never
          minted answers EMPTY. A field Kraken ignored would have answered identically to
          the control, and it did not.
          `ClosedOrders {'cl_ord_id': K}` also works — and returned TWO orders for one key
          (OCU3L6-C56FR-5BBVGF and OD6OFT-CXNPX-Z6GVNP for `p5669_1`), because a CLOSE reuses
          its entry's counter. So a lookup BY KEY is ambiguous by construction and #487 cannot
          treat one key as naming one order.

      Q1b OUR PARSE LAYER CANNOT READ ITS OWN ANSWER. `parse_query_response(raw, key, ts)`
          returns UNKNOWN while `parse_query_response(raw, txid, ts)` returns FILLED for the
          SAME payload — it looks the entry up as `raw.get(broker_ref)` and Kraken keys its
          answer by txid. A query-by-key path must parse by the txid it got back.

      Q2  RETENTION IS FAR LONGER THAN THE EARLIER MEASUREMENT. 70 of 70 still answerable,
          0 gone; the oldest is from 2026-09-08 07:00, i.e. ~4.5 DAYS. The previous entry in
          this series (txid-retention probe, 2026-09-08) reached only 70-93 minutes. The edge
          is still not found — it is beyond 4.5 days, not at it.

      Q3  Settled without a call, and confirmed by Q1's ClosedOrders answer: two orders share
          the key `p5669_1`, so uniqueness is NOT enforced across history.

      Q4  CancelOrder REFUSES a gone order, and the refusals are DISTINGUISHABLE:
              never minted (OZZZZZ-ZZZZZ-ZZZZZZ)  → ['EOrder:Invalid order']
              archived, venue says closed         → ['EOrder:Unknown order']
          Caveat, not yet separated: `Invalid order` may be a FORMAT rejection rather than
          "no such order" — the synthetic reference is shape-valid to our regex but Kraken may
          validate more. A never-minted reference of Kraken's own minting would separate them.
          No `{'count': 0}` occurred, so `parse_cancel_response`'s unconditional CANCELLED is
          not reachable on this path; what IS reachable is the error, which the §43 ladder
          classifies TERMINAL — an already-gone cancel therefore ends as a venue fault.
          Not measured: the 'canceled' flavour — no archived subject came back with that
          status in this run.

    2026-09-13 08:55 UTC — SUBJECT SET WIDENED, and the first run's gap was ours.
        The 2026-09-12 entry reported no `canceled` subject and left that flavour unmeasured.
        That was a defect in THIS PROBE, not a fact about Kraken: `events.csv` carries a
        broker reference only where an order produced a fill-shaped event, so a cancelled
        order never entered the harvest. Reading each run's session logs as well takes the
        set from 70 to 194.

      Q2  194 of 194 still answerable, 0 gone. Spread: 70 closed, 124 canceled. The retention
          edge is still not found, now across a wider and older population.

      Q4  THE TERMINAL FLAVOURS ARE NOT DISTINGUISHABLE. A cancelled order answers exactly
          what a filled one answers:
              never minted (OZZZZZ-ZZZZZ-ZZZZZZ)  → ['EOrder:Invalid order']
              venue says closed                   → ['EOrder:Unknown order']
              venue says canceled                 → ['EOrder:Unknown order']
          So `Invalid order` vs `Unknown order` separates NEVER MINTED from GONE, and nothing
          separates the ways of being gone. A resolution that needs to know WHICH terminal
          state an order reached must ask QueryOrders, never CancelOrder.
          `expired` remains unmeasured — the archive contains no such subject, and the probe
          now says so rather than skipping in silence.

Usage:
    python python/experiments/venue_probes/probe_kraken_order_identity.py            # archive
    python python/experiments/venue_probes/probe_kraken_order_identity.py --mode read
    python python/experiments/venue_probes/probe_kraken_order_identity.py --mode full
"""

import argparse
import csv
import json
import os
import re
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from python.configuration.market_config_manager import MarketConfigManager
from python.framework.trading_env.adapters.kraken_adapter import KrakenAdapter
from python.framework.utils.run_id_utils import (
    build_client_order_id,
    parse_client_order_id,
    session_key_from_run_id,
)

_ROOT = Path(__file__).resolve().parents[3]
_BROKER_CONFIG = _ROOT / 'configs/brokers/kraken/kraken_spot_broker_config.json'
_RUNS_DIR = _ROOT / 'runs/live'

# Kraken's own reference shape. Every payload is validated against it, so a client order id
# can never end up in a txid slot — the one confusion that could aim a cancel at a stranger.
_TXID_PATTERN = re.compile(r'^O[A-Z0-9]{5}-[A-Z0-9]{5}-[A-Z0-9]{6}$')

# A reference of Kraken's shape that Kraken never minted: zero risk by construction.
_NEVER_MINTED = 'OZZZZZ-ZZZZZ-ZZZZZZ'

# A key of OUR shape that no session could have sent — session keys come from a uuid4 half,
# so 'zzzz' is unreachable. This is the negative control Q1 stands on: without it, "Kraken
# answered" cannot be told apart from "Kraken ignored the field".
_NEVER_SENT_KEY = 'pzzzz_999999'

# QueryOrders takes a comma-separated list; kept well under the documented ceiling.
_BATCH_SIZE = 20

# A run directory written within this window suggests a live session is still going. Two
# processes on one API key share no nonce counter, and the loser of that race is the SESSION's
# write, not this probe's read.
_LIVE_SESSION_WINDOW_S = 900


class Subject:
    """One order the archive knows about, as a candidate for the venue reads."""

    def __init__(self, txid: str, client_order_id: str, run_dir: str):
        self.txid = txid
        self.client_order_id = client_order_id
        self.run_dir = run_dir


def _harvest_subjects() -> list:
    """
    Reconstruct (txid, client order id) pairs from the live run archive.

    The key is never stored — it is DERIVED, exactly as the executor derives it: the run
    directory name is the run id, its random half gives the session key, and the position id
    gives the counter. Scoped to each run's own events.csv rather than walking the tree,
    because a directory walk on this mount costs 282x an ordinary one.

    Returns:
        Subjects, oldest run first, empty when no live run has been recorded
    """
    subjects = []
    if not _RUNS_DIR.is_dir():
        return subjects

    for profile_dir in sorted(_RUNS_DIR.iterdir()):
        if not profile_dir.is_dir():
            continue
        for run_dir in sorted(profile_dir.iterdir()):
            events = run_dir / 'events.csv'
            if not events.is_file():
                continue
            session_key = session_key_from_run_id(run_dir.name)
            if not session_key:
                continue
            seen = set()
            with events.open(newline='', encoding='utf-8', errors='ignore') as handle:
                for row in csv.DictReader(handle):
                    txid = (row.get('broker_ref') or '').strip()
                    internal = (row.get('position_id') or row.get('order_id') or '').strip()
                    if not _TXID_PATTERN.match(txid) or not internal or txid in seen:
                        continue
                    key = build_client_order_id(session_key, internal)
                    if key is None:
                        continue
                    seen.add(txid)
                    subjects.append(Subject(txid, key, run_dir.name))
    return subjects


def _harvest_bare_txids() -> list:
    """
    Collect every Kraken reference the session logs mention, key or no key.

    A SECOND set, and it exists because the first one cannot answer Q4. `events.csv` carries a
    broker reference only where an order produced a fill-shaped event, so an order that was
    CANCELLED never enters it — measured: two references the archive records as cancelled were
    absent from the 70 pairs, and every one of those 70 came back `closed`. Without this the
    probe would report "no canceled subject" as a fact about the venue rather than about its
    own harvest.

    No key is derived here. Q4 asks about a reference, not about ours.

    Returns:
        Sorted unique txids, oldest run first
    """
    found = []
    seen = set()
    if not _RUNS_DIR.is_dir():
        return found
    loose = re.compile(r'O[A-Z0-9]{5}-[A-Z0-9]{5}-[A-Z0-9]{6}')
    for profile_dir in sorted(_RUNS_DIR.iterdir()):
        if not profile_dir.is_dir():
            continue
        for run_dir in sorted(profile_dir.iterdir()):
            logs = run_dir / 'session_logs'
            if not logs.is_dir():
                continue
            for log in sorted(logs.glob('*.log')):
                for txid in loose.findall(log.read_text(errors='ignore')):
                    if txid not in seen:
                        seen.add(txid)
                        found.append(txid)
    return found


def _build_adapter() -> KrakenAdapter:
    """
    Build a live-enabled Kraken adapter for private calls.

    Returns:
        KrakenAdapter with Tier-3 enabled and dry_run off
    """
    broker_config = json.loads(_BROKER_CONFIG.read_text())
    # The LIVE source, not a mirror: `configs/broker_settings/` was a leftover that
    # production stopped reading, so a probe against a real account could have run with a
    # credentials file or a transport the session itself no longer uses.
    entry = MarketConfigManager().get_broker_entry('kraken_spot')
    adapter = KrakenAdapter(broker_config)
    adapter.enable_live(
        credentials_file=entry.credentials_file,
        dry_run=False,
        transport=entry.broker_transport,
    )
    return adapter


def _call(adapter: KrakenAdapter, endpoint: str, data: dict) -> tuple:
    """
    Make one private call and return the venue's answer OR the venue's refusal.

    A Kraken error is the measurement, not a failure — it arrives as a plain ConnectionError
    carrying Kraken's own words. A transport fault is a different fact and is NOT caught here.

    Args:
        adapter: Live-enabled adapter
        endpoint: Private REST path
        data: Payload

    Returns:
        (raw dict or None, error string or None)
    """
    try:
        return adapter._fetch_private(endpoint, data), None
    except ConnectionError as exc:
        return None, str(exc)


def _refuse_if_a_session_may_be_running() -> bool:
    """
    Refuse to start while a live session may hold the same API key.

    The adapter's rate-limit lock does not span processes and the nonce is derived from the
    millisecond clock, so two processes on one key can deliver out of order. Kraken rejects
    the late arrival — and that can be the SESSION's write rather than this probe's read.

    Returns:
        True when it is safe to continue
    """
    now = time.time()
    recent = []
    if _RUNS_DIR.is_dir():
        for profile_dir in _RUNS_DIR.iterdir():
            if not profile_dir.is_dir():
                continue
            for run_dir in profile_dir.iterdir():
                if run_dir.is_dir() and (now - run_dir.stat().st_mtime) < _LIVE_SESSION_WINDOW_S:
                    recent.append(run_dir.name)
    if recent:
        print('❌ REFUSING TO START — a live run directory was written in the last '
              f'{_LIVE_SESSION_WINDOW_S // 60} minutes:')
        for name in sorted(recent):
            print(f'     {name}')
        print('   Two processes sharing one API key share no nonce counter, and the call that '
              'loses that race may be the SESSION\'s write, not this probe\'s read.')
        print('   Stop the session, or wait out the window, then re-run.')
        return False
    return True


def _print_archive(subjects: list) -> None:
    """
    Report what the archive already settles, before any call is made.

    Args:
        subjects: Harvested subjects
    """
    print('=' * 100)
    print('STAGE 0 — THE ARCHIVE (no network, no credential)')
    print('=' * 100)
    by_run = {}
    for subject in subjects:
        by_run.setdefault(subject.run_dir, []).append(subject)
    print(f'{len(subjects)} references across {len(by_run)} live runs')
    for run_dir in sorted(by_run):
        sample = by_run[run_dir][0]
        print(f'  {run_dir}: {len(by_run[run_dir]):>3} refs   '
              f'e.g. {sample.txid} ← {sample.client_order_id}')
    print()
    print('Q3 needs no call: a CLOSE reuses its entry\'s counter, so every position sends its '
          'key at least twice by construction.')
    print('    The archive therefore contains accepted re-uses; the single recorded refusal had '
          'its colliding sibling still RESTING.')
    print()


def _stage_census(adapter: KrakenAdapter) -> tuple:
    """
    Read the open orders and build the exclusion list every later stage obeys.

    Args:
        adapter: Live-enabled adapter

    Returns:
        (set of resting txids, raw answer)
    """
    print('=' * 100)
    print('STAGE 1 — OPEN ORDER CENSUS (1 read)')
    print('=' * 100)
    raw, error = _call(adapter, '/0/private/OpenOrders', {})
    if error:
        print(f'❌ census failed: {error}')
        return None, None

    open_orders = (raw or {}).get('open', {})
    resting = set(open_orders.keys())
    print(f'{len(resting)} order(s) resting at the venue')
    for txid, info in open_orders.items():
        key = info.get('cl_ord_id') or info.get('userref') or ''
        ours = ' ← OURS' if parse_client_order_id(str(key)) else ''
        print(f'  {txid}  {info.get("status", "?"):<10} key={key or "-"}{ours}')
    if not resting:
        print('  (none — nothing can be excluded because nothing is exposed)')
    print()
    return resting, raw


def _stage_retention(adapter: KrakenAdapter, subjects: list, resting: set) -> dict:
    """
    Extend the retention series: which archived references does Kraken still describe?

    Args:
        adapter: Live-enabled adapter
        subjects: Harvested subjects
        resting: Currently resting txids, reported separately

    Returns:
        Mapping txid → status string for every reference the venue still knows
    """
    print('=' * 100)
    print('STAGE 2 — Q2: RETENTION EDGE (reads, batched)')
    print('=' * 100)
    seen = {s.txid for s in subjects}
    candidates = [s.txid for s in subjects if s.txid not in resting]
    # The wider set adds the flavours events.csv cannot carry — a cancelled order never
    # produced a fill-shaped event, so it is in the logs and nowhere else.
    candidates += [t for t in _harvest_bare_txids()
                   if t not in seen and t not in resting]
    known = {}
    for start in range(0, len(candidates), _BATCH_SIZE):
        batch = candidates[start:start + _BATCH_SIZE]
        raw, error = _call(adapter, '/0/private/QueryOrders',
                           {'txid': ','.join(batch), 'trades': 'false'})
        if error:
            print(f'  batch {start // _BATCH_SIZE + 1}: refused — {error}')
            continue
        for txid, info in (raw or {}).items():
            known[txid] = info.get('status', '?')

    gone = [t for t in candidates if t not in known]
    print(f'{len(known)} of {len(candidates)} archived references still answerable, '
          f'{len(gone)} already gone')
    # The distribution decides what Q4 can measure: it needs one subject per terminal
    # flavour, and a flavour the archive does not contain cannot be asked about.
    spread = {}
    for status in known.values():
        spread[status] = spread.get(status, 0) + 1
    print(f'  status spread: {spread}')
    # The EDGE, never a span: a min/max pair would read as a range in which everything holds.
    for subject in subjects:
        if subject.txid in known:
            print(f'  oldest still known:  {subject.txid}  ({subject.run_dir}) '
                  f'status={known[subject.txid]}')
            break
    for subject in subjects:
        if subject.txid in gone:
            print(f'  youngest already gone: {subject.txid}  ({subject.run_dir})')
            break
    print()
    return known


def _stage_key_lookup(adapter: KrakenAdapter, subjects: list, known: dict) -> None:
    """
    Q1: can we ask about an order by the key WE minted?

    Args:
        adapter: Live-enabled adapter
        subjects: Harvested subjects
        known: txid → status for references the venue still describes
    """
    print('=' * 100)
    print('STAGE 3 — Q1: DOES KRAKEN ACCEPT OUR KEY? (reads)')
    print('=' * 100)
    subject = next((s for s in subjects if s.txid in known), None)
    if subject is None:
        print('  no still-answerable reference to ask about — skipped')
        print()
        return

    print(f'  subject: {subject.txid}  key={subject.client_order_id}  ({subject.run_dir})')
    now = datetime.now(timezone.utc)

    control, error = _call(adapter, '/0/private/QueryOrders', {'txid': subject.txid})
    print(f'  1 control   txid only          → '
          f'{"refused: " + error if error else sorted((control or {}).keys())}')

    with_key, error = _call(adapter, '/0/private/QueryOrders',
                            {'txid': subject.txid, 'cl_ord_id': subject.client_order_id})
    print(f'  2 our key   txid + cl_ord_id   → '
          f'{"refused: " + error if error else sorted((with_key or {}).keys())}')

    # The load-bearing half. Kraken is known to ignore fields it does not recognise — the
    # adapter documents exactly that trap for `stopprice` — so "it answered" proves nothing
    # unless a key that CANNOT match produces a different answer.
    negative, error = _call(adapter, '/0/private/QueryOrders',
                            {'txid': subject.txid, 'cl_ord_id': _NEVER_SENT_KEY})
    print(f'  3 control   txid + wrong key   → '
          f'{"refused: " + error if error else sorted((negative or {}).keys())}')

    closed, error = _call(adapter, '/0/private/ClosedOrders',
                          {'cl_ord_id': subject.client_order_id})
    if error:
        print(f'  4 ClosedOrders by our key    → refused: {error}')
    else:
        entries = (closed or {}).get('closed', {})
        print(f'  4 ClosedOrders by our key    → {len(entries)} entr(y/ies): '
              f'{sorted(entries.keys())}')

    print()
    print('  VERDICT   2 names the order and 3 is empty      → Kraken ACCEPTS our key')
    print('            2 and 3 answer identically            → Kraken IGNORES the field')
    print('            2 is refused with Invalid arguments   → Kraken REJECTS the field')
    print()
    # What production would make of the same answer: parse_query_response looks the entry up
    # by the reference it was given, and Kraken keys its answer by txid — so asking by key
    # and parsing by key returns UNKNOWN even when the venue did answer.
    if with_key is not None:
        parsed = adapter.parse_query_response(with_key, subject.client_order_id, now)
        print(f'  production parse, keyed by OUR key → {parsed.status}')
        parsed_txid = adapter.parse_query_response(with_key, subject.txid, now)
        print(f'  production parse, keyed by txid    → {parsed_txid.status}')
    print()


def _stage_cancel_a_gone_order(
    adapter: KrakenAdapter,
    subjects: list,
    known: dict,
    resting: set,
) -> None:
    """
    Q4: what does CancelOrder answer for an order that is already gone?

    Every subject is verified TERMINAL in this same run before any cancel is sent, and a
    reference that could not be read is refused rather than assumed. Terminal is absorbing,
    so there is no race to lose.

    Args:
        adapter: Live-enabled adapter
        subjects: Harvested subjects
        known: txid → status for references the venue still describes
        resting: Currently resting txids — excluded unconditionally
    """
    print('=' * 100)
    print('STAGE 4 — Q4: CANCEL AN ORDER THAT IS ALREADY GONE (writes that create nothing)')
    print('=' * 100)

    targets = [(_NEVER_MINTED, 'never minted by Kraken')]
    for status in ('closed', 'canceled', 'expired'):
        txid = next(
            (t for t, s in known.items() if s == status and t not in resting), None)
        if txid is not None:
            targets.append((txid, f'archived, venue says {status}'))
        else:
            print(f'  (no {status} subject in the archive — that flavour stays unmeasured)')

    for txid, why in targets:
        if not _TXID_PATTERN.match(txid) or txid in resting:
            print(f'  ⛔ refused to send {txid} — failed validation or currently resting')
            continue
        raw, error = _call(adapter, '/0/private/CancelOrder', {'txid': txid})
        if error:
            print(f'  {txid}  ({why})')
            print(f'      → REFUSED: {error}')
        else:
            print(f'  {txid}  ({why})')
            print(f'      → answered: {raw}')
            if (raw or {}).get('count') == 0:
                print('      ⚠️  count=0 with NO error — production reads this as CANCELLED, '
                      'because parse_cancel_response never looks at count.')
        # A write is never retried. One call per subject, whatever came back.
    print()


def _verify_no_residue(adapter: KrakenAdapter, before: set) -> None:
    """
    Prove the probe left nothing behind, rather than asserting it.

    Args:
        adapter: Live-enabled adapter
        before: Resting txids from the opening census
    """
    print('=' * 100)
    print('CLEANUP PROOF — open orders, before vs after')
    print('=' * 100)
    raw, error = _call(adapter, '/0/private/OpenOrders', {})
    if error:
        print(f'❌ could not re-read open orders: {error} — VERIFY BY HAND')
        return
    after = set((raw or {}).get('open', {}).keys())
    if after == before:
        print(f'✅ unchanged, {len(after)} order(s) resting — the probe created and removed '
              'nothing')
    else:
        print('❗ DIFFERENT — inspect immediately')
        print(f'   appeared: {sorted(after - before)}')
        print(f'   vanished: {sorted(before - after)}')
    print()


def main() -> None:
    """Run the probe in the requested mode."""
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    parser.add_argument('--mode', choices=('archive', 'read', 'full'), default='archive',
                        help='archive: no network · read: private reads · full: adds cancels')
    args = parser.parse_args()

    if os.environ.get('FINIEX_CONFIG_ISOLATION') not in (None, '', '0'):
        print('❌ FINIEX_CONFIG_ISOLATION is set — the run would read the tracked placeholder '
              'credentials and measure nothing while looking like a failed API.')
        return

    subjects = _harvest_subjects()
    _print_archive(subjects)
    if not subjects:
        print('No live run has been recorded — nothing to ask about. Stopping before any '
              'network call.')
        return
    if args.mode == 'archive':
        print('Mode `archive`: stopping here. Re-run with --mode read to ask the venue.')
        return

    if not _refuse_if_a_session_may_be_running():
        return

    adapter = _build_adapter()
    resting, _ = _stage_census(adapter)
    if resting is None:
        print('Stopping: without the census nothing may be excluded, and exclusion is what '
              'makes the later stages safe.')
        return

    known = _stage_retention(adapter, subjects, resting)
    _stage_key_lookup(adapter, subjects, known)

    if args.mode == 'full':
        _stage_cancel_a_gone_order(adapter, subjects, known, resting)

    _verify_no_residue(adapter, resting)
    print('Record the answers in this file\'s MEASUREMENTS section, dated, beside the older '
          'ones.')


if __name__ == '__main__':
    main()
