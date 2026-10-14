"""
Nothing committed rests on external data (#561, decision 6).

A contributor works from the project's published dataset, which never carries a venue's published
history: a committed scenario set reaching into that history finds nothing there, and a generator
profile derived from it re-derives to different numbers. The rule stands in CONTRIBUTING.md; this
suite makes it checked rather than trusted, over every scenario set and generator profile in the
folders the repository keeps them in — a local file there is one `git add` away from committed.
"""

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Set, Tuple
from unittest.mock import MagicMock

import pandas as pd
import pytest

from python.configuration.import_config_manager import ImportConfigManager
from python.framework.batch.requirements_collector import RequirementsCollector
from python.framework.config_directory.config_directory_builder import config_kind_of
from python.framework.data_preparation.shared_data_preparator import SharedDataPreparator
from python.framework.logging.bootstrap_logger import get_global_logger
from python.framework.types.config_directory_types import ConfigKind
from python.framework.types.config_types.data_origin_config_types import OriginClass
from python.framework.utils.time_utils import ensure_utc_aware
from python.scenario.scenario_config_loader import ScenarioConfigLoader

_ROOT = Path(__file__).resolve().parents[3]
_FOLDERS = [_ROOT / 'configs', _ROOT / 'tests' / 'fixtures']

# The day the class `external` came into existence. A profile generated before it cannot rest on
# external data, because none existed — which is why such a profile may lack `origin_classes`.
_EXTERNAL_INTRODUCED = datetime(2026, 10, 13, tzinfo=timezone.utc)


def _generator_profiles() -> List[Tuple[Path, Dict]]:
    """
    Every generator profile below the kept folders, recognised by its `profile_meta` block.

    Returns:
        (path, profile_meta) per profile
    """
    found = []
    for folder in _FOLDERS:
        for path in sorted(folder.rglob('*.json')):
            try:
                data = json.loads(path.read_text(encoding='utf-8'))
            except (json.JSONDecodeError, UnicodeDecodeError):
                continue
            if isinstance(data, dict) and isinstance(data.get('profile_meta'), dict):
                found.append((path, data['profile_meta']))
    return found


def _scenario_sets() -> List[Path]:
    """
    Every scenario set below the kept folders, by the configuration directory's own marker.

    Returns:
        Their paths
    """
    return [path for folder in _FOLDERS for path in sorted(folder.rglob('*.json'))
            if config_kind_of(path) is ConfigKind.SCENARIO_SET]


def _external_ticks() -> pd.DataFrame:
    """
    The external tick files of the local archive, read from the index without rebuilding it.

    Returns:
        Their broker, symbol and span — empty when the archive holds none
    """
    index_path = Path(ImportConfigManager().get_import_output_path()) / 'ticks_index.parquet'
    if not index_path.exists():
        return pd.DataFrame(columns=['broker_type', 'symbol', 'start_time', 'end_time'])
    frame = pd.read_parquet(index_path, columns=[
        'broker_type', 'symbol', 'start_time', 'end_time', 'origin_class'])
    frame = frame[frame['origin_class'] == OriginClass.EXTERNAL.value]
    frame['start_time'] = pd.to_datetime(frame['start_time'], utc=True)
    frame['end_time'] = pd.to_datetime(frame['end_time'], utc=True)
    return frame


class TestCommittedGeneratorProfiles:

    def test_the_folders_hold_profiles_to_check(self):
        assert _generator_profiles()

    @pytest.mark.parametrize('path,meta', _generator_profiles(),
                             ids=lambda value: value.name if isinstance(value, Path) else '')
    def test_no_profile_is_derived_from_external_data(self, path, meta):
        if 'origin_classes' not in meta:
            generated = datetime.fromisoformat(meta['generated_at'])
            assert generated < _EXTERNAL_INTRODUCED, (
                f'{path.name} records no origin classes although it was generated after external '
                f'data existed — regenerate it')
            return
        assert OriginClass.EXTERNAL.value not in meta['origin_classes'], (
            f'{path.name} was derived from a venue\'s published history — keep it off the '
            f'repository (CONTRIBUTING.md, "Data Behind Committed Files")')


class TestCommittedScenarioSets:
    """
    Asked of the LOCAL archive: whether a set's ticks or its warmup reach external data depends
    on what the archive holds. Where it holds none — on a contributor's machine, and on ours
    before the import — there is nothing a set could reach, and the case says so by skipping.
    """

    def test_no_scenario_reads_or_warms_up_from_external_data(self):
        external = _external_ticks()
        if external.empty:
            pytest.skip('The local archive holds no external data — nothing a set could reach')

        external_symbols: Set[Tuple[str, str]] = set(
            zip(external['broker_type'], external['symbol']))
        logger = get_global_logger()
        preparator = SharedDataPreparator(MagicMock())
        violations = []
        unloadable = []

        for path in _scenario_sets():
            try:
                scenarios = ScenarioConfigLoader().load_config(str(path)).scenarios
            except Exception as error:
                # A fixture built to be refused is not this rule's business
                unloadable.append(f'{path.name}: {type(error).__name__}')
                continue
            scenarios = [scenario for scenario in scenarios
                         if (scenario.data_broker_type, scenario.symbol) in external_symbols]
            if not scenarios:
                continue

            for scenario in scenarios:
                start = pd.Timestamp(ensure_utc_aware(scenario.start_date))
                # A tick-count scenario has no end; external data lies only before our
                # collection, so what it could reach is the file holding its start
                end = (pd.Timestamp(ensure_utc_aware(scenario.end_date))
                       if scenario.end_date else start + pd.Timedelta(microseconds=1))
                reached = external[
                    (external['broker_type'] == scenario.data_broker_type)
                    & (external['symbol'] == scenario.symbol)
                    & (external['start_time'] < end) & (external['end_time'] > start)]
                if not reached.empty:
                    violations.append(f'{path.name} · {scenario.name}: its ticks')

            requirements = RequirementsCollector(logger=logger).collect_and_validate(scenarios)
            bars, _, _ = preparator.prepare_bars(requirements.bar_requirements, first_tick_times={})
            for (scenario_name, timeframe), selected in bars.items():
                scenario = next(s for s in scenarios if s.name == scenario_name)
                if preparator._warmup_reaches_external(
                        scenario.data_broker_type, scenario.symbol, timeframe, selected):
                    violations.append(f'{path.name} · {scenario_name}: its {timeframe} warmup')

        assert not violations, (
            'Committed scenario sets reach a venue\'s published history — move their windows '
            'after our collection starts, or shorten the warmup:\n  ' + '\n  '.join(violations)
            + (f'\n(not loadable, not checked: {", ".join(unloadable)})' if unloadable else ''))
