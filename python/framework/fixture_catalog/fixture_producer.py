"""
Produce a fixture catalog entry and check it (#576).

A backtest and a sweep run in this process, through the same entry points the strategy runner
and the optimization CLI use. An AutoTrader session runs as the AutoTrader command line does —
in a process of its own, because a session that must never reach its close can only be produced
by killing one. Every run lands in the ordinary stores: the catalog's runs ARE the product.

A production finds its runs by asking the run index which runs appeared while it ran and belong
to the entry — by run name, or by sweep — checks them through the readers the API serves from, and
records itself, verified or not, in the production record. A run of the same name that somebody
starts elsewhere while a production runs would be counted as the production's; produce when the
machine is otherwise quiet.
"""

import copy
import json
import shutil
import subprocess
import sys
import tempfile
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, Iterator, List, Optional, Set

from python.api.api_contract import API_CONTRACT_VERSION
from python.configuration.app_config_manager import AppConfigManager
from python.configuration.autotrader.autotrader_config_loader import load_autotrader_config
from python.framework.fixture_catalog.fixture_evidence_reader import read_fixture_evidence
from python.framework.fixture_catalog.fixture_production_store import FixtureProductionStore
from python.framework.optimization.optimization_runner import OptimizationRunner
from python.framework.reporting.io.artifact_specs import ORDER_EVENTS_STREAM
from python.framework.reporting.io.order_event_stream_io import read_order_event_stream
from python.framework.reporting.store.run_index import RunIndex
from python.framework.types.api.report_types import ParentKind, RunInfo
from python.framework.types.fixture_catalog_types import (
    FixtureEntry,
    FixtureProducerKind,
    FixtureProduction,
    FixtureSession,
)
from python.framework.types.log_layout_types import IO_SUBDIR
from python.framework.types.run_origin_types import RunChannel
from python.scenario.scenario_config_loader import ScenarioConfigLoader
from python.scenario.scenario_strategy_runner import initialize_batch_and_run

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_AUTOTRADER_CLI = _PROJECT_ROOT / 'python' / 'cli' / 'autotrader_cli.py'
# Where a production's profiles and carry-over live while it runs — outside the repository.
_WORKSPACE_ROOT = Path(tempfile.gettempdir()) / 'finiex_fixture_productions'

# How long a session that is to be killed may take to reach the moment it is killed at. A whole
# demo session takes about 42 s here (measured 2026-10-10); the limit only bounds one that never
# gets there, on a machine slowed by other work.
_KILL_WAIT_LIMIT_SECONDS = 180.0
_KILL_POLL_SECONDS = 0.5

# Runs an AutoTrader session from the given command-line arguments. When a kill moment is given —
# a question asked while the session runs — the session is killed the moment it answers yes.
# Returns a one-line outcome for the console and the production record.
SessionRunner = Callable[[List[str], Optional[Callable[[], bool]]], str]


def run_autotrader_session(arguments: List[str],
                           kill_when: Optional[Callable[[], bool]]) -> str:
    """
    Run one AutoTrader session through its command line, in a process of its own.

    Args:
        arguments: The arguments after `autotrader_cli.py`
        kill_when: Asked while the session runs; it is killed the moment this answers yes, so it
            never reaches its close. None runs it to its end

    Returns:
        A one-line outcome — with the tail of what the session wrote to stderr when it failed
    """
    command = [sys.executable, str(_AUTOTRADER_CLI), *arguments]
    if kill_when is None:
        result = subprocess.run(command, cwd=_PROJECT_ROOT, capture_output=True, text=True)
        if result.returncode == 0:
            return 'exit 0'
        return f'exit {result.returncode} — {_stderr_tail(result.stderr)}'
    return run_until_killed(command, kill_when, _KILL_WAIT_LIMIT_SECONDS)


def run_until_killed(command: List[str], kill_when: Callable[[], bool], limit_seconds: float,
                     poll_seconds: float = _KILL_POLL_SECONDS) -> str:
    """
    Run a process and kill it the moment a question answers yes — or at a limit, when it never
    does.

    A kill cannot leave a word in the process it ends, so the reason travels in what this returns:
    killed when due, killed at the limit, or ended on its own before either.

    Args:
        command: The process to run
        kill_when: Asked every poll while the process runs
        limit_seconds: How long to wait for a yes before killing it anyway
        poll_seconds: How often to ask

    Returns:
        A one-line outcome naming why the process ended
    """
    started = time.monotonic()
    with tempfile.TemporaryFile(mode='w+') as stderr:
        process = subprocess.Popen(command, cwd=_PROJECT_ROOT, stdout=subprocess.DEVNULL,
                                   stderr=stderr)
        try:
            while process.poll() is None:
                elapsed = time.monotonic() - started
                if kill_when():
                    return f'killed when due, after {elapsed:.1f} s'
                if elapsed > limit_seconds:
                    return f'never due within {limit_seconds:.0f} s — killed at the limit'
                time.sleep(poll_seconds)
            stderr.seek(0)
            tail = _stderr_tail(stderr.read())
            return (f'ended on its own before it was due (exit {process.returncode})'
                    + (f' — {tail}' if tail else ''))
        finally:
            # Also on an interrupt during the wait — a session left running would go on writing
            # into the stores after the production that started it has ended.
            if process.poll() is None:
                process.kill()
            process.wait()


def _stderr_tail(stderr: str) -> str:
    """
    The last lines a process wrote to stderr, joined into one.

    Args:
        stderr: What it wrote

    Returns:
        Its last three lines
    """
    return ' | '.join(stderr.strip().splitlines()[-3:])


class FixtureProducer:
    """Produces catalog entries, checks them, and records each production."""

    def __init__(self, session_runner: SessionRunner = run_autotrader_session,
                 store: Optional[FixtureProductionStore] = None,
                 progress: Callable[[str], None] = print):
        """
        Args:
            session_runner: How an AutoTrader session is run — a test hands in one that runs none
            store: The production record; the configured one when not given
            progress: Where a line about each step goes
        """
        self._session_runner = session_runner
        self._store = store or FixtureProductionStore()
        self._progress = progress
        self._session_outcomes: List[str] = []

    def produce(self, entry: FixtureEntry) -> FixtureProduction:
        """
        Produce an entry's runs, check them, and record the production.

        Args:
            entry: The catalog entry

        Returns:
            The recorded production
        """
        before = {run.run_id for run in self._runs()}
        self._session_outcomes = []
        sweep_ids: List[str] = []
        run_name = ''
        if entry.producer == FixtureProducerKind.SCENARIO_SET:
            run_name = self._run_scenario_set(entry.source)
        elif entry.producer == FixtureProducerKind.SWEEP:
            sweep_ids.append(OptimizationRunner().run(str(_PROJECT_ROOT / entry.source)))
        elif entry.producer == FixtureProducerKind.PROFILE:
            run_name = load_autotrader_config(str(_PROJECT_ROOT / entry.source)).profile_name
            self._run_profile(entry, run_name)
        else:
            run_name = entry.session_profile_name
            self._run_session_sequence(entry)

        runs = _runs_of(entry, [run for run in self._runs() if run.run_id not in before],
                        run_name, set(sweep_ids))
        deployment_ids = sorted({run.parent_id for run in runs
                                 if run.parent_kind == ParentKind.DEPLOYMENT and run.parent_id})
        failed = failed_properties(entry, [run.run_id for run in runs], deployment_ids, sweep_ids)
        production = FixtureProduction(
            entry_id=entry.entry_id,
            produced_at=datetime.now(timezone.utc).isoformat(),
            run_ids=sorted(run.run_id for run in runs),
            deployment_ids=deployment_ids,
            sweep_ids=sweep_ids,
            verified=bool(runs) and not failed,
            failed_properties=failed,
            session_outcomes=list(self._session_outcomes),
            report_contract=API_CONTRACT_VERSION,
        )
        self._store.append(production)
        return production

    def _runs(self) -> List[RunInfo]:
        """
        Every run the configured index lists.

        Returns:
            The runs
        """
        return self._run_index().list_runs()

    def _run_index(self) -> RunIndex:
        """
        The configured run index.

        Returns:
            The index
        """
        return RunIndex(AppConfigManager().get_file_logging_config_object().run_index)

    def _first_order_placed(self, run_name: str) -> Callable[[], bool]:
        """
        A question to ask while a session runs: has the next run of this name — one that appears
        after this call — written its first order event?

        It reads the session's own order event stream, the record a consumer reads too. The
        moment matters: the session is past its boot and trading, so the run it leaves behind
        dies with an order on it, the way a real session dies.

        Args:
            run_name: The name the session's run is listed under

        Returns:
            The question
        """
        index = self._run_index()
        before = {run.run_id for run in index.list_runs()}

        def placed() -> bool:
            frame = index.read()
            if frame.empty:
                return False
            new = frame[(frame['run_name'] == run_name) & ~frame['run_id'].isin(before)]
            for run_dir in new['run_dir']:
                stream = _PROJECT_ROOT / run_dir / IO_SUBDIR / ORDER_EVENTS_STREAM
                if stream.exists() and read_order_event_stream(stream)[0]:
                    return True
            return False

        return placed

    def _run_scenario_set(self, source: str) -> str:
        """
        Run one backtest of a scenario set, the way the strategy runner does.

        Args:
            source: The set, relative to the project root

        Returns:
            The set's name — the run name its run is listed under
        """
        loaded = ScenarioConfigLoader().load_config(str(_PROJECT_ROOT / source))
        initialize_batch_and_run(loaded, AppConfigManager(), channel=RunChannel.CLI)
        return loaded.scenario_set_name

    def _run_profile(self, entry: FixtureEntry, run_name: str) -> None:
        """
        Run one AutoTrader session of a profile, with a carry-over of its own.

        Args:
            entry: The catalog entry
            run_name: The profile's name, printed with the outcome
        """
        base = json.loads((_PROJECT_ROOT / entry.source).read_text(encoding='utf-8'))
        with _production_workspace(entry) as workspace:
            # The profile's own file name, so the run records the configuration it came from.
            path = workspace / Path(entry.source).name
            path.write_text(json.dumps(isolated_profile(base, workspace), indent=2),
                            encoding='utf-8')
            outcome = self._session_runner(['run', '--config', str(path)], None)
            self._session_outcomes.append(f'{run_name}: {outcome}')
            self._progress(f'   {run_name}: {outcome}')

    def _run_session_sequence(self, entry: FixtureEntry) -> None:
        """
        Run every session of a sequence, one after the other, each from its own profile and all
        of them sharing one carry-over — the one a deployment's identity travels in.

        Args:
            entry: The catalog entry
        """
        base = json.loads((_PROJECT_ROOT / entry.source).read_text(encoding='utf-8'))
        with _production_workspace(entry) as workspace:
            for index, session in enumerate(entry.sessions, start=1):
                profile = session_profile(isolated_profile(base, workspace), entry, session)
                path = workspace / f'{entry.session_profile_name}_s{index}.json'
                path.write_text(json.dumps(profile, indent=2), encoding='utf-8')
                arguments = ['run', '--config', str(path)]
                if session.new_deployment:
                    arguments.append('--new-deployment')
                kill_when = (self._first_order_placed(entry.session_profile_name)
                             if session.killed_before_close else None)
                started = time.monotonic()
                outcome = self._session_runner(arguments, kill_when)
                if session.killed_before_close:
                    outcome = f'to be killed at its first order: {outcome}'
                self._session_outcomes.append(f'{session.label}: {outcome}')
                self._progress(f'   {session.label:44} {time.monotonic() - started:5.1f}s  '
                               f'{outcome}')


@contextmanager
def _production_workspace(entry: FixtureEntry) -> Iterator[Path]:
    """
    A directory for one production's profiles and the carry-over its sessions write, emptied
    before the production and removed after it.

    The same path every time, so a production's profiles — and the configuration ids they
    register — are the same every time; outside the repository, so nothing it holds outlives
    the production.

    Args:
        entry: The catalog entry

    Returns:
        The workspace
    """
    workspace = _WORKSPACE_ROOT / entry.entry_id
    shutil.rmtree(workspace, ignore_errors=True)
    workspace.mkdir(parents=True)
    try:
        yield workspace
    finally:
        shutil.rmtree(workspace, ignore_errors=True)


def isolated_profile(base: Dict[str, Any], workspace: Path) -> Dict[str, Any]:
    """
    A profile whose carry-over lives in a production's own workspace.

    Without it a production inherits whatever its bot carried over from every earlier run —
    the booking periods it continues counting, the drawdown it restores — so a "fresh" history
    would not be fresh and two productions of one entry would differ by machine state.

    Args:
        base: The profile, parsed
        workspace: The production's workspace

    Returns:
        A copy with both carry-over stores pointed into the workspace
    """
    profile = copy.deepcopy(base)
    profile.setdefault('cold_start', {})['path'] = str(workspace / 'cold_start_state')
    profile.setdefault('state_persistence', {})['path'] = str(workspace / 'session_state')
    return profile


def failed_properties(entry: FixtureEntry, run_ids: List[str], deployment_ids: List[str],
                      sweep_ids: List[str]) -> List[str]:
    """
    Check an entry's properties against the runs of one production.

    Args:
        entry: The catalog entry
        run_ids: The runs the production made
        deployment_ids: The deployments its sessions belong to
        sweep_ids: The sweeps it ran

    Returns:
        The ids of the properties that did not hold, in catalog order
    """
    evidence = read_fixture_evidence(run_ids, deployment_ids, sweep_ids)
    return [prop.property_id for prop in entry.properties if not prop.check(evidence)]


def session_profile(base: Dict[str, Any], entry: FixtureEntry,
                    session: FixtureSession) -> Dict[str, Any]:
    """
    The profile one session of a sequence runs from: the base profile under the sequence's bot,
    replaying the session's own day, with the session's changes set.

    Args:
        base: The base profile, parsed
        entry: The sequence's catalog entry
        session: The session

    Returns:
        The session's profile
    """
    profile = copy.deepcopy(base)
    profile['profile_name'] = entry.session_profile_name
    profile['bot_id'] = entry.session_bot_id
    # The sessions of one bot form one history only when the profile declares it (#497).
    profile['deployment'] = {'continuous': True}
    profile['scenario_settings']['max_ticks'] = entry.session_max_ticks
    profile['scenario_settings']['start_date'] = session.start_date
    for dotted, value in session.overrides.items():
        target = profile
        *parents, key = dotted.split('.')
        for parent in parents:
            target = target.setdefault(parent, {})
        target[key] = value
    return profile


def _runs_of(entry: FixtureEntry, new_runs: List[RunInfo], run_name: str,
             sweep_ids: Set[str]) -> List[RunInfo]:
    """
    The runs that appeared during a production and belong to its entry.

    Args:
        entry: The catalog entry
        new_runs: Every run that appeared while it ran
        run_name: The name its runs are listed under; unused for a sweep
        sweep_ids: The sweeps it ran

    Returns:
        The entry's runs
    """
    if entry.producer == FixtureProducerKind.SWEEP:
        return [run for run in new_runs if run.parent_id in sweep_ids]
    return [run for run in new_runs if run.name == run_name]
