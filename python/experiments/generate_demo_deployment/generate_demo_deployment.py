"""
Produce a deployment worth looking at — for #539's routes and whoever renders them.

The API for a live bot's history shipped against a deployment of three mock sessions with one
booking period each, every figure `0.0` and every timestamp identical. A consumer said it
plainly: the traps the documentation warns about are unreachable from their side, so nothing
they build can be tested. This makes one that can.

**PRODUCED, never hand-written.** CLAUDE.md's rule for the analogous case is explicit — data
anomalies are produced by running the system, never carved into an archive by hand — and the
reason transfers exactly: a hand-written ledger is a fiction that can drift from what we
actually emit, and then a consumer builds against a shape that does not exist. So this runs
real sessions through the real CLI and lets the real coordinators write the rows.

**Re-runnable on purpose** (§27 / §32): the shapes it produces move when the API contract moves,
so a fixture generated once rots silently. It serves #539 and dies with it unless another issue
claims it.

WHAT IT PRODUCES, and each one is a case a renderer has to survive:

    deployment A   4 sessions   a strategy change between 2 and 3  (param_hash moves)
                                an operation change between 3 and 4 (profile_hash moves)
                                several booking periods per session (the replay crosses midnight)
                                real trades, so P&L and drawdown are not zero
    deployment B   1 session    the same bot after --new-deployment: TWO histories, one bot_id
    plus           1 session    killed before its close, so `unfinished` is non-zero

WHAT IT CANNOT PRODUCE: a meaningful `gap_hours`. The gap is derived from two wall-clock stamps,
so sessions run in one sitting are seconds apart. A long idle stretch has to be waited for, and
inventing one would mean writing the ledger by hand — the thing this exists not to do.

WHERE IT WRITES: the ordinary dev stores — `runs/live/`, `runs/ledger/`, and the cold-start
carry-over the deployment identity travels in. It adds; it deletes nothing.

    python python/experiments/generate_demo_deployment/generate_demo_deployment.py
"""

import json
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional

# The one mock profile that actually trades: its RSI thresholds were widened to 40/60 for
# exactly that reason, measured 2026-09-14. A session that books no trade produces a period
# row of zeroes, which is the state this script exists to get out of.
BASE_PROFILE = Path('configs/autotrader_profiles/backtesting/trade_lifecycle_test.json')

BOT_ID = 'demo-btcusd-bot'
PROFILE_NAME = 'demo_btcusd_bot'

# Long enough to cross the replay's midnight, so a session books more than its closing period.
# Measured 2026-09-23: the first attempt used 40 000 from 12:00 and reached 23:33 — the data
# in this era carries ~3 600 ticks an hour, so it never reached the boundary and every session
# booked ONE period. Starting in the evening with more ticks puts midnight inside the window.
TICKS_FULL = 90_000
# Each session replays its OWN day, one after the next. Without this every session replayed the
# same window and booked periods with IDENTICAL timestamps — four bars stacked on one another,
# which a Gantt renders as four bots running at once. The sessions RAN sequentially; it was
# their DATA that did not, and the booking period carries the data's clock, not the wall clock.
FIRST_WINDOW = '2026-02-01T18:00:00+00:00'
# The session that gets killed has to live long enough to REGISTER — the run header is written
# at start, and a kill before it leaves nothing behind at all, which is not the case we want to
# produce. Measured 2026-09-23: at 8 s with the wider window the kill landed before the header
# and `unfinished` stayed 0; the sessions take ~66 s end to end.
KILL_AFTER_SECONDS = 25.0


def _window(day_offset: int) -> str:
    """
    The replay window this session starts at — one day later than the session before it.

    Args:
        day_offset: How many days after the first window

    Returns:
        An ISO-8601 UTC start date
    """
    return (datetime.fromisoformat(FIRST_WINDOW)
            + timedelta(days=day_offset)).isoformat()


def _profile(
    tmp: Path,
    label: str,
    rsi_oversold: int,
    safety_max_drawdown_pct: float,
    day_offset: int,
    max_ticks: int = TICKS_FULL,
) -> Path:
    """
    Write one demo profile variant.

    Args:
        tmp: Directory the variant is written to
        label: Suffix for the file name, so a failed run can be traced to its config
        rsi_oversold: Moves `param_hash` — what the bot DECIDES
        safety_max_drawdown_pct: Moves `profile_hash` — what the session DOES
        day_offset: Which replay day this session gets, so the sessions are CONSECUTIVE in
            data time and not four copies of one window
        max_ticks: How much of the replay to consume

    Returns:
        Path to the written profile
    """
    profile: Dict[str, Any] = json.loads(BASE_PROFILE.read_text())
    profile['name'] = PROFILE_NAME
    # Mandatory for a continuous deployment (#538): without it the state of a restarted bot is
    # filed under the profile NAME, and renaming the profile orphans it.
    profile['bot_id'] = BOT_ID
    profile['deployment'] = {'continuous': True}
    profile['strategy_config']['decision_logic_config']['rsi_oversold'] = rsi_oversold
    profile['safety'] = {
        'enabled': True,
        'max_drawdown_pct': safety_max_drawdown_pct,
    }
    profile['scenario_settings']['max_ticks'] = max_ticks
    profile['scenario_settings']['start_date'] = _window(day_offset)
    path = tmp / f'demo_{label}.json'
    path.write_text(json.dumps(profile, indent=2))
    return path


def _run(profile: Path, extra: Optional[List[str]] = None,
         kill_after: Optional[float] = None) -> str:
    """
    Run one session through the real CLI.

    Args:
        profile: The profile to run
        extra: Extra CLI flags (`--new-deployment`)
        kill_after: Seconds after which to kill it — the way to produce a session that never
            reaches its close, which is what leaves a run-index entry with no ledger row (§44)

    Returns:
        A one-line outcome for the console
    """
    command = [sys.executable, 'python/cli/autotrader_cli.py', 'run',
               '--config', str(profile), *(extra or [])]
    if kill_after is None:
        result = subprocess.run(command, capture_output=True, text=True)
        return f'exit {result.returncode}'

    process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    time.sleep(kill_after)
    process.kill()
    process.wait()
    return 'KILLED mid-session'


def main() -> int:
    """
    Produce the demo deployment.

    Returns:
        Process exit code
    """
    if not BASE_PROFILE.exists():
        print(f'❌ base profile not found: {BASE_PROFILE}')
        return 1

    print('\n🎬 Generating a demo deployment — real sessions, real coordinators, real rows.')
    print(f'   bot_id: {BOT_ID}   profile: {PROFILE_NAME}\n')

    with tempfile.TemporaryDirectory() as raw:
        tmp = Path(raw)
        # Sessions 1 and 2: one configuration. 3: the strategy moves. 4: only the operation.
        steps = [
            # A FRESH history: the bot identity is the same, so without this the sessions
            # would join whatever deployment the carry-over still names — correct for a
            # real restart, and not what a demo run wants.
            ('s1', _profile(tmp, 's1', 40, 25.0, day_offset=0), ['--new-deployment'], None),
            ('s2', _profile(tmp, 's2', 40, 25.0, day_offset=1), None, None),
            ('s3 · STRATEGY changed', _profile(tmp, 's3', 35, 25.0, day_offset=2), None, None),
            ('s4 · OPERATION changed', _profile(tmp, 's4', 35, 12.0, day_offset=3), None, None),
            ('s5 · killed before its close',
             _profile(tmp, 's5', 35, 12.0, day_offset=4), None, KILL_AFTER_SECONDS),
            ('s6 · a SECOND history for the same bot',
             _profile(tmp, 's6', 35, 12.0, day_offset=5), ['--new-deployment'], None),
        ]
        for label, profile, extra, kill_after in steps:
            started = time.monotonic()
            outcome = _run(profile, extra, kill_after)
            print(f'   {label:44} {outcome:20} {time.monotonic() - started:5.1f}s')

    print('\n✅ Done. What was produced:')
    print('     python python/cli/run_index_cli.py deployments')
    print('     python python/cli/run_index_cli.py deployments --id <the newer one>')
    print('   Over HTTP: /api/v1/deployments · /{id} · /{id}/booking-periods\n')
    return 0


if __name__ == '__main__':
    sys.exit(main())
