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
from python.framework.reporting.store.run_index import RunIndex
from python.framework.types.api.report_types import ParentKind, RunInfo
from python.framework.types.fixture_catalog_types import (
    FixtureEntry,
    FixtureProducerKind,
    FixtureProduction,
    FixtureSession,
)
from python.framework.types.run_origin_types import RunChannel
from python.scenario.scenario_config_loader import ScenarioConfigLoader
from python.scenario.scenario_strategy_runner import initialize_batch_and_run

_PROJECT_ROOT = Path(__file__).resolve().parents[3]
_AUTOTRADER_CLI = _PROJECT_ROOT / 'python' / 'cli' / 'autotrader_cli.py'
# Where a production's profiles and carry-over live while it runs — outside the repository.
_WORKSPACE_ROOT = Path(tempfile.gettempdir()) / 'finiex_fixture_productions'

# Runs an AutoTrader session from the given command-line arguments and kills it after the given
# seconds when they are set. Returns a one-line outcome for the console.
SessionRunner = Callable[[List[str], Optional[float]], str]


def run_autotrader_session(arguments: List[str], kill_after_seconds: Optional[float]) -> str:
    """
    Run one AutoTrader session through its command line, in a process of its own.

    Args:
        arguments: The arguments after `autotrader_cli.py`
        kill_after_seconds: Kill it after this long, so it never reaches its close; None runs it
            to its end

    Returns:
        A one-line outcome for the console — with the tail of what the session wrote to stderr
        when it failed
    """
    command = [sys.executable, str(_AUTOTRADER_CLI), *arguments]
    if kill_after_seconds is None:
        result = subprocess.run(command, cwd=_PROJECT_ROOT, capture_output=True, text=True)
        if result.returncode == 0:
            return 'exit 0'
        tail = ' | '.join(result.stderr.strip().splitlines()[-3:])
        return f'exit {result.returncode} — {tail}'
    process = subprocess.Popen(command, cwd=_PROJECT_ROOT, stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
    try:
        time.sleep(kill_after_seconds)
    finally:
        # Also on an interrupt during the wait — a session left running would go on writing
        # into the stores after the production that started it has ended.
        process.kill()
        process.wait()
    return f'killed after {kill_after_seconds:.0f} s'


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

    def produce(self, entry: FixtureEntry) -> FixtureProduction:
        """
        Produce an entry's runs, check them, and record the production.

        Args:
            entry: The catalog entry

        Returns:
            The recorded production
        """
        before = {run.run_id for run in self._runs()}
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
        return RunIndex(AppConfigManager().get_file_logging_config_object().run_index).list_runs()

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
                started = time.monotonic()
                outcome = self._session_runner(arguments, session.kill_after_seconds)
                self._progress(f'   {session.label:44} {outcome:20} '
                               f'{time.monotonic() - started:5.1f}s')


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
