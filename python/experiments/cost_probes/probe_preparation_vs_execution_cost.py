"""Probe: how does a backtest's time divide between preparing data and executing it? (#672)

The question arose in #672, which owns the probe. A run prepares its data — index and coverage,
requirements, the parquet reads — and then walks the ticks. The issue asks whether the preparation
is large enough that overlapping it with the previous run's execution would be worth building, and
it asks for the figure before anything is designed.

It answers that in three sections, in one unattended command.

The FILE SELECTION section needs no run at all. A batch groups its scenarios by venue and symbol
and reads the files of the ONE span that covers every one of them, so windows lying far apart make
it read everything in between. Both counts come from the index selection the loader itself calls,
and the ratio between them is the largest single term a preparation can carry — invisible from the
phase durations, which only say how long the reading took.

The FILESYSTEM section reads the same tick files from two places, alternating and several times
over, and reports what one file costs on each. On this development container the data sits on a 9p
mount of the Windows drive, where a read is capped at 64 KB per round trip; a Linux filesystem pays
neither that nor the per-file cost. It is the cheap, load-robust half: seconds per round, and no
batch noise around it. It is also, on the figures below, the SMALL term — which is worth knowing
before anyone moves a data home to make a backtest faster.

The BATCH section runs whole scenario sets through the ordinary path (`run_scenario_batch`) and
reads the figures from the summary that path returns: the warmup phases one by one, the tick run
time, and what the hand-off to the subprocesses cost. Each set runs against both data homes, round
by round, each in a subprocess whose `FINIEX_DATA_HOME` is that home, the order flipping every
round so the noise falls on both.

Three sets answer different halves of the issue, and they are built to differ in one thing at a
time. `backtesting_loadtest_40_scenarios` is forty windows of a few hours: many scenarios, so the
preparation that overlapping could hide is spread over the most execution, and it is the set the
release benchmark measures, which makes its figures comparable against a committed certificate.
`backtesting_four_long_windows` is four windows of a whole day in one week, the same strategy and
venue, so only the shape differs: fewer scenarios, more ticks each, and a preparation that has
almost nothing to hide behind. `backtesting_four_long_windows_split_year` is those same four
windows plus one half a year later — the shape of a study holding its out-of-sample window far from
its in-sample ones — and it exists to be read against the set before it, because between them lies
nothing but one scenario and the span it stretches.

Per arm the minimum, median and maximum of every phase are printed, never a median alone: three
runs that disagree must say so.

**It refuses rather than produce a void number.** A run under a debugger is serial and carries
trace overhead, and the report itself then says its timings are not representative — two runs were
measured that way on 2026-11-25 before anyone noticed. So the probe stops if it detects one. It
also stops when the second data home is missing, rather than measuring one arm twice.

**It writes.** Unlike its siblings, each batch measurement is a real run: a run directory, a ledger
row and an entry in the run index, under whichever data home the arm names. Runs taken against the
ordinary home therefore stay in the operator's run tree and are theirs to keep or remove. Beyond
those and its own result file, nothing is touched but the global log header every start of the
framework writes.

**It needs a quiet machine and cannot tell whether it has one.** The container sees neither the
Windows host's load nor what other containers do. Measured 2026-11-25 on a busy machine, the same
read of the same files swung 60 % within two minutes. So it is run the way the release benchmark is
run: in a window cleared for it, after a restart, with nothing else started. It records what it CAN
see — the processors bound, how busy they were, the memory — beside every measurement, so a figure
taken under load can be recognised later instead of being averaged in.

There is no sweep section, and that is a decision rather than an omission. A sweep prepares its
data once and reuses that mount across its combinations, so there is no repeated preparation left
in it to measure; and where a sweep does run into a wall it is memory rather than time.

Recorded results:
  2026-11-26, on a machine in ordinary use — indicative only, taken while sizing the sets, never
  a measurement in the sense above:
    one tick file read raw from the 9p mount    35-41 ms, about 29 MB/s, 50,000 ticks per file
    four windows of one day inside one week     warmup  3.05 s against a tick run of 50.8 s
    the same four plus one half a year later    warmup 27.5 s against a tick run of 58.2 s
  The second pair is the whole point: one scenario more, and the loader reads 434 files instead of
  19 because the span now covers six months. Of that warmup, 97 % was the tick parquet phase, and
  of THAT the raw file reads were under a tenth — so most of a preparation is the work after the
  read, done once, serially, while the cores that will run the scenarios stand idle.

Usage, one command, unattended:
    python python/experiments/cost_probes/probe_preparation_vs_execution_cost.py \
        --sets backtesting/backtesting_loadtest_40_scenarios.json,\
backtesting/backtesting_four_long_windows.json,\
backtesting/backtesting_four_long_windows_split_year.json \
        --other-home /root/finiex_data --rounds 3 \
        --out _issue_briefs/672_measurement.jsonl
"""

import argparse
import json
import os
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from python.framework.types.run_origin_types import RunChannel
from python.framework.utils.machine_resources_reader import MachineResourcesReader
from python.framework.utils.runtime_env_utils import is_debug_execution
from python.data_management.index.tick_index_manager import TickIndexManager
from python.scenario.scenario_strategy_runner import run_scenario_batch

# The reader takes its first processor sample when it is built, so a reading in the same breath has
# no window to measure and reports nothing at all. A short pause buys a real figure, once per run.
_MACHINE_WINDOW_S = 0.3

# The ordinary home is the working directory, which the resolver means when the variable is unset.
_ORDINARY = 'ordinary'

_DATA_HOME_ENV = 'FINIEX_DATA_HOME'

# How many files one filesystem round reads, and how many rounds it takes. Small enough that a
# round costs seconds, repeated often enough that the spread is visible rather than inferred.
# The archive's own layout below a data home — what two homes have in common
_ARCHIVE_ANCHOR = ('data', 'processed')

# Where a scenario set file is named from, the way the batch runner names it
_SET_ROOT = 'configs/scenario_sets'

_FS_FILES_PER_ROUND = 8
_FS_ROUNDS = 6


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _machine_now() -> Dict[str, object]:
    """
    What the machine reports about itself at this moment.

    Returns:
        The processors bound, how busy they were over the probe's window, and the memory in use
    """
    reader = MachineResourcesReader()
    time.sleep(_MACHINE_WINDOW_S)
    reading = reader.read()
    memory = reader.read_memory()
    return {
        'cpu_count': reading.cpu.count,
        'cpu_used_pct': reading.cpu.used_pct,
        'cpu_window_s': reading.cpu.window_s,
        'memory_used_pct': memory.used_pct,
        'measured_at': reading.measured_at.isoformat(),
    }


def _refuse(reason: str) -> None:
    """
    Stop before measuring, naming what is wrong and what would make it right.

    Args:
        reason: What the run would have measured instead of the truth
    """
    print(f'\n  REFUSED — {reason}\n')
    sys.exit(2)


def _check_second_home_index(other_home: str) -> None:
    """
    Hold the second home's tick index to existing and to naming its OWN files.

    The index stores ABSOLUTE paths, and the index FILE is read from the data home while the paths
    inside it still name the tree the index was BUILT in. A home copied from another one therefore
    sends every read back to the original, and both arms measure one filesystem — which is what
    happened on 2026-11-26, where eight of nine rounds simply showed whichever arm ran second,
    warmed by the first. Nothing in the run says so, which is why it is checked here.

    Args:
        other_home: The second data home
    """
    archive = Path(other_home).joinpath(*_ARCHIVE_ANCHOR)
    index = TickIndexManager(data_dir=str(archive))
    index.build_index(check_stale=False)

    paths = [entry['path'] for symbols in index.index.values()
             for entries in symbols.values() for entry in entries]
    if not paths:
        _refuse(f'the tick index under {archive} is empty or absent, so this arm has no archive to '
                f'read and would fail scenario by scenario. Build it there first.')

    foreign = next((p for p in paths if not str(p).startswith(str(other_home))), None)
    if foreign is not None:
        _refuse(f'the tick index under {archive} names {foreign}, a file in another tree. The index '
                f'stores absolute paths, so this arm would read every tick file from there and both '
                f'arms would measure one filesystem. Rebuild it against this home first, in python: '
                f'TickIndexManager(data_dir=\'{archive}\').build_index(force_rebuild=True)')


def _check_conditions(other_home: str) -> None:
    """
    Hold the conditions a measurement cannot recover from afterwards.

    Args:
        other_home: The second data home the batch section compares against
    """
    if is_debug_execution():
        _refuse('a debugger is attached or DEBUG_MODE is set. Execution would be serial and carry '
                'trace overhead, and every timing would be void. Run this from a terminal.')
    if other_home == _ORDINARY:
        return
    if not Path(other_home).is_dir():
        _refuse(f'the second data home {other_home} is not a directory. Without it only one arm '
                'exists, and the comparison the probe is for cannot be made.')
    _check_second_home_index(other_home)


def _read_files(paths: List[Path]) -> float:
    """
    Read every file whole and return the seconds it took.

    Args:
        paths: The files to read

    Returns:
        The elapsed seconds, on the monotonic clock
    """
    start = time.perf_counter()
    for path in paths:
        with open(path, 'rb') as handle:
            while handle.read(1 << 20):
                pass
    return time.perf_counter() - start


def _mirrored(path: Path, other_dir: Path) -> Optional[Path]:
    """
    The same archive file under the second data home.

    The tick index holds absolute paths into the home it was built in, and a second home mirrors
    the archive's own layout below data/processed — so the part from that folder onwards is what
    the two homes have in common.

    Args:
        path: An archive file, as the index names it
        other_dir: The second data home

    Returns:
        The mirrored file, or None where the second home does not hold it
    """
    parts = path.parts
    for index in range(len(parts) - 1):
        if parts[index:index + 2] == _ARCHIVE_ANCHOR:
            candidate = other_dir.joinpath(*parts[index:])
            return candidate if candidate.is_file() else None
    return None


def _filesystem_section(index: TickIndexManager, other_dir: Path,
                        offset: int) -> List[Dict[str, object]]:
    """
    Alternate reads between the two filesystems, on files neither side has read yet.

    The page cache cannot be dropped from inside this container — /proc/sys/vm/drop_caches is
    read only, and posix_fadvise(DONTNEED) has no effect on the 9p mount (both measured
    2026-11-26) — so the only cold read available is of a file nothing has read yet. Each round
    therefore takes a fresh slice and reads each side's OWN copy rather than copying one across:
    a copy reads its source, which warms exactly the cache the measurement is about. The two
    sides are distinct files, so neither warms the other, and the order flips every round.

    The slice comes from the TICK index, so every file measured is one a preparation would
    actually read — around a megabyte each. Striding across the index rather than taking
    neighbours keeps one symbol's run of same-sized files from standing for the archive. A file
    the HOST already holds in its own cache cannot be recognised from here; that would make the
    9p side look faster, never slower.

    The sample is DETERMINISTIC, so the same invocation measures the same files twice and a
    second run in the same boot reads them from the cache — which is what --fs-offset is for: it
    moves the sample to a part of the archive this boot has not touched.

    Args:
        index: The tick index, already loaded
        other_dir: The second data home, which mirrors the archive
        offset: How many files to skip before sampling, so a second run reads untouched ones

    Returns:
        One record per round, naming the milliseconds per file and the rate on each side
    """
    pool: List[Path] = []
    for symbols in index.index.values():
        for entries in symbols.values():
            pool.extend(Path(entry['path']) for entry in entries)
    pool.sort()

    wanted = _FS_FILES_PER_ROUND * _FS_ROUNDS
    if len(pool) < wanted:
        print(f'  filesystem section skipped: {len(pool)} tick files in the index, {wanted} needed')
        return []

    stride = len(pool) // wanted
    paired: List[Tuple[Path, Path]] = []
    for position in range(wanted):
        here = pool[(offset + position * stride) % len(pool)]
        there = _mirrored(here, other_dir)
        if there is not None and here.is_file():
            paired.append((here, there))
    if len(paired) < _FS_FILES_PER_ROUND:
        print(f'  filesystem section skipped: only {len(paired)} of {wanted} sampled files are '
              f'present under both homes')
        return []

    records: List[Dict[str, object]] = []
    for round_no in range(_FS_ROUNDS):
        batch = paired[round_no * _FS_FILES_PER_ROUND:(round_no + 1) * _FS_FILES_PER_ROUND]
        if not batch:
            break
        here = [a for a, _ in batch]
        there = [b for _, b in batch]
        megabytes = sum(a.stat().st_size for a in here) / 1e6
        if round_no % 2 == 0:
            a_s, b_s = _read_files(here), _read_files(there)
        else:
            b_s, a_s = _read_files(there), _read_files(here)
        records.append({
            'round': round_no + 1,
            'files': len(batch),
            'megabytes': megabytes,
            'source_ms_per_file': a_s / len(batch) * 1000,
            'other_ms_per_file': b_s / len(batch) * 1000,
            'source_mb_per_s': megabytes / a_s if a_s else None,
            'other_mb_per_s': megabytes / b_s if b_s else None,
            'machine': _machine_now(),
        })
        last = records[-1]
        print(f'  fs round {round_no + 1}: {len(batch)} files, {megabytes:.1f} MB · '
              f'today {last["source_ms_per_file"]:.1f} ms/file '
              f'({last["source_mb_per_s"]:.0f} MB/s) · '
              f'other {last["other_ms_per_file"]:.1f} ms/file '
              f'({last["other_mb_per_s"]:.0f} MB/s)')
    return records


def _stamp(declared: str) -> datetime:
    """
    Read a scenario's declared boundary as an aware instant.

    Args:
        declared: The date as the scenario set writes it

    Returns:
        The same instant, in UTC
    """
    return datetime.fromisoformat(declared).astimezone(timezone.utc)


def _selection_section(sets: List[str],
                       index: TickIndexManager) -> List[Dict[str, object]]:
    """
    Per set: how many files the loader will read, against how many its windows need.

    A batch groups its scenarios by venue and symbol and then reads the files of the ONE span
    that covers every one of them. Windows lying far apart therefore make it read everything
    between them, which is invisible from the phase durations alone and can be the largest term
    in a preparation. Both counts come from the index selection the loader itself calls, so the
    figure is the loader's and not a second reading of the index. Neither includes the lookback
    the warmup bars add, which is the same on both sides.

    Args:
        sets: The scenario set files, as the caller named them
        index: The tick index, already loaded

    Returns:
        One record per set, venue and symbol, with the two counts and the span they come from
    """
    records: List[Dict[str, object]] = []

    for scenario_set in sets:
        scenarios = json.loads(
            (Path(_SET_ROOT) / scenario_set).read_text(encoding='utf-8'))['scenarios']
        grouped: Dict[Tuple[str, str], List[Dict[str, object]]] = {}
        for scenario in scenarios:
            if not scenario.get('enabled', True):
                continue
            key = (scenario['data_broker_type'], scenario['symbol'])
            grouped.setdefault(key, []).append(scenario)

        for (broker_type, symbol), group in grouped.items():
            starts = [_stamp(str(s['start_date'])) for s in group]
            ends = [_stamp(str(s['end_date'])) for s in group]
            span = index.get_relevant_files(broker_type=broker_type, symbol=symbol,
                                            start_date=min(starts), end_date=max(ends))
            needed = set()
            for scenario in group:
                needed.update(index.get_relevant_files(
                    broker_type=broker_type, symbol=symbol,
                    start_date=_stamp(str(scenario['start_date'])),
                    end_date=_stamp(str(scenario['end_date']))))
            records.append({
                'set': scenario_set, 'broker_type': broker_type, 'symbol': symbol,
                'scenarios': len(group),
                'files_the_span_covers': len(span),
                'files_the_windows_need': len(needed),
                'span_start': min(starts).isoformat(), 'span_end': max(ends).isoformat(),
            })
    return records


def _measure(scenario_set: str) -> Dict[str, object]:
    """
    Run one batch in this process, on the data home its environment names.

    Args:
        scenario_set: The scenario set file, as the caller named it

    Returns:
        The warmup phases with their seconds, the three batch times, and the hand-off figures
    """
    before = _machine_now()
    summary = run_scenario_batch(scenario_set, channel=RunChannel.DIRECT)
    if summary is None:
        return {'failed': True, 'machine_before': before}

    return {
        'failed': False,
        'phases': [{'name': p.name, 'duration_s': p.duration_s} for p in summary.warmup_phases],
        'warmup_s': summary.batch_warmup_time,
        'tickrun_s': summary.batch_tickrun_time,
        'execution_s': summary.batch_execution_time,
        'pickle_s': summary.batch_pickle_time,
        'pickle_sample_mb': summary.batch_pickle_sample_mb,
        'machine_before': before,
        'machine_after': _machine_now(),
    }


def _run_arm(home: str, scenario_set: str) -> Optional[Dict[str, object]]:
    """
    Measure one arm in a subprocess whose data home is the one named.

    Args:
        home: The data home for this arm, or the ordinary one
        scenario_set: The scenario set file to run

    Returns:
        What the subprocess measured, or None when it did not finish
    """
    env = {**os.environ}
    if home == _ORDINARY:
        env.pop(_DATA_HOME_ENV, None)
    else:
        env[_DATA_HOME_ENV] = home

    out = subprocess.run(
        [sys.executable, __file__, '--measure-one', '--sets', scenario_set],
        capture_output=True, text=True, env=env, check=False)
    for line in reversed(out.stdout.splitlines()):
        if line.startswith('{'):
            return json.loads(line)
    tail = (out.stderr or out.stdout).strip().splitlines()[-3:]
    print(f'    arm {home}: no measurement returned (exit {out.returncode})')
    for line in tail:
        print(f'      {line[:140]}')
    return None


def _batch_section(sets: List[str], homes: List[str], rounds: int) -> List[Dict[str, object]]:
    """
    Run every set against both homes, round by round, the order flipping each round.

    Args:
        sets: The scenario set files to run
        homes: The two data homes to compare
        rounds: How many times each arm runs per set

    Returns:
        One record per measurement, each naming its set, arm and round
    """
    records: List[Dict[str, object]] = []
    for scenario_set in sets:
        print(f'\nset {scenario_set}')
        for round_no in range(rounds):
            order = homes if round_no % 2 == 0 else list(reversed(homes))
            for home in order:
                print(f'  round {round_no + 1}, arm {home} …', flush=True)
                measured = _run_arm(home, scenario_set)
                if measured is not None and not measured.get('failed'):
                    records.append({'set': scenario_set, 'round': round_no + 1,
                                    'home': home, **measured})
    return records


def _spread(values: List[float]) -> str:
    """
    The three numbers a single median would hide.

    Args:
        values: The seconds one phase took across the rounds of one arm

    Returns:
        Minimum, median and maximum, formatted
    """
    if not values:
        return '—'
    return f'{min(values):6.2f} {statistics.median(values):6.2f} {max(values):6.2f}'


def _report(fs_records: List[Dict[str, object]], batch_records: List[Dict[str, object]],
            selection_records: List[Dict[str, object]], sets: List[str],
            homes: List[str]) -> None:
    """
    Print the three sections as minimum, median and maximum, and the share that decides the issue.

    Args:
        fs_records: What the filesystem section measured
        batch_records: What the batch section measured
        selection_records: What each set's file selection covers against what it needs
        sets: The scenario sets, in the order they ran
        homes: The arms, in the order they are printed
    """
    if selection_records:
        print('\nFILE SELECTION — what the loader reads against what the windows need')
        print(f'  {"set":52.52} {"venue/symbol":14.14}{"covered":>9}{"needed":>8}{"factor":>8}')
        for rec in selection_records:
            covered = int(rec['files_the_span_covers'])
            needed = int(rec['files_the_windows_need'])
            factor = covered / needed if needed else 0.0
            name = str(rec['set']).removeprefix('backtesting/').removesuffix('.json')
            print(f'  {name:52.52} '
                  f'{rec["broker_type"] + "/" + str(rec["symbol"]):14.14}'
                  f'{covered:>9}{needed:>8}{factor:>7.1f}x')

    if fs_records:
        here = [float(r['source_ms_per_file']) for r in fs_records]
        there = [float(r['other_ms_per_file']) for r in fs_records]
        here_rate = [float(r['source_mb_per_s']) for r in fs_records if r['source_mb_per_s']]
        there_rate = [float(r['other_mb_per_s']) for r in fs_records if r['other_mb_per_s']]
        ratios = [a / b for a, b in zip(here, there) if b]
        print('\nFILESYSTEM — reading whole tick files, min / median / max')
        print(f'  today, ms per file          {_spread(here)}')
        print(f'  the other home, ms per file {_spread(there)}')
        print(f'  today, MB per second        {_spread(here_rate)}')
        print(f'  the other home, MB/s        {_spread(there_rate)}')
        if ratios:
            print(f'  factor, today over other    {min(ratios):6.2f} '
                  f'{statistics.median(ratios):6.2f} {max(ratios):6.2f}')

    for scenario_set in sets:
        rows = [r for r in batch_records if r['set'] == scenario_set]
        if not rows:
            continue
        names: List[str] = []
        for rec in rows:
            for phase in rec['phases']:
                if phase['name'] not in names:
                    names.append(phase['name'])

        print(f'\nBATCH — {scenario_set}')
        print(f'{"":44}' + ''.join(f'{h[:22]:>24}' for h in homes))
        print(f'{"":44}' + ''.join(f'{"min    med    max":>24}' for _ in homes))
        for name in names:
            row = f'  {name:42.42}'
            for home in homes:
                vals = [p['duration_s'] for r in rows if r['home'] == home
                        for p in r['phases'] if p['name'] == name]
                row += f'{_spread(vals):>24}'
            print(row)

        for label, key in (('Total warmup', 'warmup_s'), ('Execution (tick run)', 'tickrun_s'),
                           ('Pickle (hand-off)', 'pickle_s')):
            row = f'  {label:42.42}'
            for home in homes:
                row += f'{_spread([float(r[key]) for r in rows if r["home"] == home]):>24}'
            print(row)

        for home in homes:
            arm = [r for r in rows if r['home'] == home]
            if not arm:
                continue
            shares = [float(r['warmup_s']) / (float(r['warmup_s']) + float(r['tickrun_s'])) * 100
                      for r in arm]
            print(f'  preparation share of the run, {home}: '
                  f'{min(shares):.1f} — {statistics.median(shares):.1f} — {max(shares):.1f} %'
                  f'   ({len(arm)} run(s))')


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--sets', required=True,
                        help='The scenario set files to run, comma separated')
    parser.add_argument('--other-home', default=_ORDINARY,
                        help='The second data home, on the other filesystem')
    parser.add_argument('--rounds', type=int, default=3,
                        help='How many times each arm runs per set')
    parser.add_argument('--fs-offset', type=int, default=0,
                        help='Move the filesystem sample this many files along the archive. The '
                             'sample is deterministic, so a second run in the same boot would '
                             'read the first run\'s files back out of the page cache')
    parser.add_argument('--host-cpu', type=float, default=None,
                        help='What the host reported as its processor load before the window; the '
                             'container cannot see it')
    parser.add_argument('--comment', default='',
                        help='What the person running it should know later — power mode, what else ran')
    parser.add_argument('--out', default=None,
                        help='Where to append the measurements as one JSON line per run')
    parser.add_argument('--measure-one', action='store_true',
                        help='Internal: measure a single batch and print it as JSON')
    args = parser.parse_args()

    sets = [s.strip() for s in args.sets.split(',') if s.strip()]

    if args.measure_one:
        print(json.dumps(_measure(sets[0])))
        return

    _check_conditions(args.other_home)

    homes = [_ORDINARY, args.other_home] if args.other_home != _ORDINARY else [_ORDINARY]
    machine = _machine_now()
    print(f'started {_now()}')
    print(f'  sets        {", ".join(sets)}')
    print(f'  arms        {" against ".join(homes)}')
    print(f'  rounds      {args.rounds}')
    print(f'  processors  {machine["cpu_count"]}, busy {machine["cpu_used_pct"]} %, '
          f'memory {machine["memory_used_pct"]} %')
    print(f'  host        {args.host_cpu if args.host_cpu is not None else "not given"}'
          f'{"   " + args.comment if args.comment else ""}')
    print(f'  fs sample   offset {args.fs_offset} — valid only on files this boot has not read; '
          f'raise it to measure again without a restart')

    index = TickIndexManager()
    index.build_index(check_stale=False)
    selection_records = _selection_section(sets, index)

    fs_records: List[Dict[str, object]] = []
    if len(homes) > 1:
        print('\nfilesystem section')
        fs_records = _filesystem_section(index, Path(args.other_home), args.fs_offset)

    batch_records = _batch_section(sets, homes, args.rounds)
    if not batch_records and not fs_records:
        print('nothing measured')
        return

    _report(fs_records, batch_records, selection_records, sets, homes)
    print(f'\nfinished {_now()}')

    if args.out:
        header = {'kind': 'probe_header', 'at': _now(), 'sets': sets, 'homes': homes,
                  'rounds': args.rounds, 'host_cpu_pct': args.host_cpu,
                  'comment': args.comment, 'machine': machine}
        with open(args.out, 'a', encoding='utf-8') as handle:
            handle.write(json.dumps(header) + '\n')
            for rec in fs_records:
                handle.write(json.dumps({'kind': 'filesystem', **rec}) + '\n')
            for rec in batch_records:
                handle.write(json.dumps({'kind': 'batch', **rec}) + '\n')
            for rec in selection_records:
                handle.write(json.dumps({'kind': 'selection', **rec}) + '\n')
        print(f'appended {len(fs_records) + len(batch_records) + len(selection_records) + 1} '
              f'line(s) to {args.out}')


if __name__ == '__main__':
    main()
