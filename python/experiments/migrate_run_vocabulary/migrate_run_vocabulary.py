"""
One-time migration for the vocabulary renames of 2026-09-28.

Single-use, like every migration in this tree: it rewrites what is on disk to the names the code
reads now, and is not a code path anything imports.

What it rewrites:
  configs      a scenario set's `name` → `scenario_name` (every scenario); an AutoTrader profile's
               `name` → `profile_name` and its `scenario_settings.name` → `scenario_name`. The key is
               replaced in the TEXT, so a file keeps its formatting; each result is parsed back and
               checked before it is written. Covers configs/, tests/fixtures/, user_configs/ and
               every configured user algo directory — each changed file is listed by path.
  runs         runs/live/ → runs/autotrader/, `run_type` "live" → "autotrader" in every header, and
               the ledger fragments: the same value in `run_type` plus the renamed columns. The
               stored report artifacts (`io/*.json`) get the booking-period keys `segment_*` →
               `period_*`, in the TEXT and parsed back like the configurations. Then the run index
               and the ledger index are rebuilt, both from what is on disk.
  carry_over   the cold-start carry-over's `highest_segment_no` → `highest_period_no`, then its
               index is rebuilt.
  probes       (2026-09-29) the CORE test components moved from `core/backtesting/` to
               `core/test_probes/` under names without "backtesting", and their worker instance
               `backtesting_worker` became `probe_worker`: the old strings are replaced in the stored
               report artifacts (text, parsed back) and in the ledger fragments' text columns, then
               the ledger index is rebuilt. Log files keep what they logged.
  data_brokers (2026-09-29) `data_source` meant two things. A stale-data stress event's key
               becomes `stale_data_source` in every configuration (only inside
               `stale_data_stress.events`, checked by parsing the file back). The stored report
               artifacts name the broker a unit read its ticks from: `data_source` →
               `data_broker_type` and `sentiment_source` → `data_sentiment_type` in
               `portfolio.json` and `scenario_details.json`, whose roll-up `data_sources` →
               `data_brokers` keyed by `data_broker_type`. Text replaced, parsed back.

The run-config store (run_configs/) is left alone on purpose: its index cannot be rebuilt without
loss, its frozen copies are old versions and stay true of the runs that named them, and a changed
file registers as a new version the next time it runs.

Usage:
    python python/experiments/migrate_run_vocabulary/migrate_run_vocabulary.py --preview
    python python/experiments/migrate_run_vocabulary/migrate_run_vocabulary.py
    python python/experiments/migrate_run_vocabulary/migrate_run_vocabulary.py --only configs

Every part may run again: what is already renamed is left as it is.
"""

import argparse
import json
import re
import shutil
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from python.configuration.app_config_manager import AppConfigManager
from python.framework.reporting.store.run_index import RunIndex
from python.framework.reporting.store.run_results_ledger import LEDGER_COLUMNS
from python.framework.persistence.cold_start_state_index import ColdStartStateIndex
from python.framework.reporting.store.run_ledger_index import RunLedgerIndex
from python.framework.store.store_registrations import build_registrations
from python.framework.types.store_types import StoreId

_CONFIG_ROOTS = [Path('configs'), Path('tests/fixtures'), Path('user_configs')]
_NAME_KEY = re.compile(r'"name"(\s*:)')

# Ledger columns renamed with the vocabulary (old → new).
LEDGER_COLUMN_RENAMES: Dict[str, str] = {
    'config_snapshot': 'strategy_config_json',
    **{f'segment_{field}': f'period_{field}' for field in (
        'no', 'opened_at', 'closed_at', 'close_reason', 'max_equity', 'min_equity',
        'max_drawdown')},
}
# The same booking-period keys where they are stored as JSON: report artifacts and the carry-over.
_PERIOD_KEY = re.compile(
    r'"(highest_)?segment_(no|opened_at|closed_at|close_reason|max_equity|min_equity|max_drawdown)"')
_OLD_RUN_TYPE = 'live'
_NEW_RUN_TYPE = 'autotrader'

# The CORE test components renamed on 2026-09-29 (old string → new string).
PROBE_RENAMES: Dict[str, str] = {
    **{f'CORE/backtesting/backtesting_{old}': f'CORE/test_probes/{new}' for old, new in (
        ('deterministic', 'deterministic_probe'), ('event_probe', 'event_probe'),
        ('margin_stress', 'margin_stress_probe'), ('multi_position', 'multi_position_probe'),
        ('outage_probe', 'outage_probe'), ('sample_worker', 'sample_probe_worker'))},
    'backtesting_worker': 'probe_worker',
}
_PROBE_TOKEN = re.compile('|'.join(re.escape(old) for old in PROBE_RENAMES) + r'(?![A-Za-z0-9_])')

# The two meanings of `data_source`, split on 2026-09-29.
_STRESS_SOURCE_KEY = re.compile(r'"data_source"(\s*:)')
_ARTIFACT_KEY_RENAMES: Dict[str, Dict[str, str]] = {
    'portfolio.json': {'data_source': 'data_broker_type', 'sentiment_source': 'data_sentiment_type'},
    'scenario_details.json': {'data_source': 'data_broker_type', 'data_sources': 'data_brokers'},
}


def _config_files() -> List[Path]:
    """
    Every JSON file a configuration can live in.

    Returns:
        The candidate files, hidden and cache directories left out
    """
    roots = list(_CONFIG_ROOTS) + [Path(d) for d in AppConfigManager().get_user_algo_dirs()]
    files: List[Path] = []
    for root in roots:
        if not root.exists():
            continue
        for path in root.rglob('*.json'):
            if any(part.startswith('.') or part in ('__pycache__', 'node_modules')
                   for part in path.parts):
                continue
            files.append(path)
    return sorted(set(files))


def _renamed_config(path: Path) -> Optional[Tuple[str, str]]:
    """
    The file's text with its retired keys renamed, checked by parsing it back.

    Args:
        path: A JSON file

    Returns:
        (kind, new text) when the file is a configuration carrying a retired key, else None
    """
    try:
        text = path.read_text(encoding='utf-8')
        data = json.loads(text)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(data, dict):
        return None

    if 'scenario_set_name' in data:
        named = sum(1 for scenario in data.get('scenarios', [])
                    if isinstance(scenario, dict) and 'name' in scenario)
        if not named:
            return None
        if len(_NAME_KEY.findall(text)) != named:
            raise ValueError(f'{path}: a "name" key sits outside the scenarios — rename it by hand')
        new_text = _NAME_KEY.sub(r'"scenario_name"\1', text)
        new = json.loads(new_text)
        if any('name' in scenario for scenario in new.get('scenarios', [])):
            raise ValueError(f'{path}: a scenario kept its "name" after the rewrite')
        return 'scenario set', new_text

    if {'symbol', 'broker_type', 'strategy_config'} <= set(data):
        settings = data.get('scenario_settings')
        top = 'name' in data
        inner = isinstance(settings, dict) and 'name' in settings
        if not (top or inner):
            return None
        if len(_NAME_KEY.findall(text)) != int(top) + int(inner):
            raise ValueError(f'{path}: a "name" key sits where no rename is defined — do it by hand')
        new_text = text
        if top:
            # The profile's own key sits at the TOP level, whatever order the file lists its
            # keys in — found by the indentation of the file's first key.
            indent = re.search(r'^\{\s*\n([ \t]*)"', text, flags=re.MULTILINE).group(1)
            new_text = re.sub(rf'^{indent}"name"(\s*:)', rf'{indent}"profile_name"\1', new_text,
                              count=1, flags=re.MULTILINE)
        new_text = _NAME_KEY.sub(r'"scenario_name"\1', new_text)
        new = json.loads(new_text)
        if 'name' in new or (top and 'profile_name' not in new) or (
                inner and 'scenario_name' not in (new.get('scenario_settings') or {})):
            raise ValueError(f'{path}: the rewrite did not land where it should — check by hand')
        return 'profile', new_text
    return None


def migrate_configs(preview: bool) -> int:
    """
    Rename the retired keys in every configuration file.

    Args:
        preview: List what would change and write nothing

    Returns:
        How many files changed (or would)
    """
    changed = 0
    for path in _config_files():
        result = _renamed_config(path)
        if result is None:
            continue
        kind, new_text = result
        changed += 1
        tracked = path.parts[0] in ('configs', 'tests')
        marker = '' if tracked else '  [your workspace]'
        print(f'  {"would rewrite" if preview else "rewrote"} {kind:<12} {path}{marker}')
        if not preview:
            path.write_text(new_text, encoding='utf-8')
    return changed


def migrate_runs(preview: bool) -> None:
    """
    Move the AutoTrader run tree, rewrite its headers and the ledger, then rebuild both indexes.

    Args:
        preview: List what would change and write nothing
    """
    file_logging = AppConfigManager().get_file_logging_config_object()
    old_root = Path('runs') / _OLD_RUN_TYPE
    new_root = Path(file_logging.run_logs.autotrader)
    if old_root.exists():
        if new_root.exists() and any(new_root.iterdir()):
            raise ValueError(f'{new_root} already holds runs — merge by hand before migrating')
        print(f'  {"would move" if preview else "moved"} {old_root} → {new_root}')
        if not preview:
            new_root.parent.mkdir(parents=True, exist_ok=True)
            if new_root.exists():
                new_root.rmdir()
            shutil.move(str(old_root), str(new_root))
    headers = list(new_root.rglob('header.json')) if new_root.exists() else []
    if preview and old_root.exists():
        headers = list(old_root.rglob('header.json'))
    rewritten = 0
    for header in headers:
        text = header.read_text(encoding='utf-8')
        new_text = re.sub(r'("run_type"\s*:\s*)"live"', r'\1"autotrader"', text)
        if new_text != text:
            rewritten += 1
            if not preview:
                header.write_text(new_text, encoding='utf-8')
    print(f'  {"would rewrite" if preview else "rewrote"} run_type in {rewritten} header(s)')

    artifacts = 0
    for artifact in Path(file_logging.run_index).parent.rglob('io/*.json'):
        if _rename_period_keys(artifact, preview):
            artifacts += 1
    print(f'  {"would rewrite" if preview else "rewrote"} the booking-period keys in {artifacts} '
          f'report artifact(s)')

    ledger_dir = Path(file_logging.run_index).parent / 'ledger'
    index = RunLedgerIndex(ledger_dir, LEDGER_COLUMNS)
    fragments = index.fragments()
    touched = 0
    for fragment in fragments:
        frame = pd.read_parquet(fragment)
        renamed = frame.rename(columns={k: v for k, v in LEDGER_COLUMN_RENAMES.items()
                                        if k in frame.columns})
        if 'run_type' in renamed.columns:
            renamed['run_type'] = renamed['run_type'].replace(_OLD_RUN_TYPE, _NEW_RUN_TYPE)
        if renamed.columns.equals(frame.columns) and renamed.equals(frame):
            continue
        touched += 1
        if not preview:
            temporary = fragment.with_suffix('.parquet.tmp')
            renamed.to_parquet(temporary, index=False)
            temporary.replace(fragment)
    print(f'  {"would rewrite" if preview else "rewrote"} {touched} of {len(fragments)} ledger fragment(s)')

    if preview:
        return
    run_count = RunIndex(Path(file_logging.run_index), file_logging.run_logs).rebuild()
    ledger_rows = index.rebuild()
    print(f'  rebuilt the run index ({run_count} runs) and the ledger index ({ledger_rows} rows)')


def _rename_period_keys(path: Path, preview: bool) -> bool:
    """
    Rename the booking-period keys in one JSON file, checked by parsing it back.

    Args:
        path: A stored JSON document
        preview: Report only, write nothing

    Returns:
        True when the file carried a retired key
    """
    text = path.read_text(encoding='utf-8')
    new_text = _PERIOD_KEY.sub(lambda m: f'"{m.group(1) or ""}period_{m.group(2)}"', text)
    if new_text == text:
        return False
    json.loads(new_text)
    if _PERIOD_KEY.search(new_text):
        raise ValueError(f'{path}: a booking-period key survived the rewrite — check by hand')
    if not preview:
        path.write_text(new_text, encoding='utf-8')
    return True


def migrate_carry_over(preview: bool) -> None:
    """
    Rename the booking-period floor in every cold-start carry-over, then rebuild its index.

    Args:
        preview: List what would change and write nothing
    """
    root = build_registrations()[StoreId.COLD_START_STATE].root
    changed = sum(1 for path in sorted(root.glob('*.json')) if _rename_period_keys(path, preview))
    print(f'  {"would rewrite" if preview else "rewrote"} {changed} carry-over document(s) in {root}')
    if changed and not preview:
        print(f'  rebuilt the carry-over index ({ColdStartStateIndex(root).rebuild()} entries)')


def _rename_probes(text: str) -> str:
    """
    Replace every renamed probe string in one text.

    Args:
        text: A stored JSON document or a ledger cell

    Returns:
        The text with the new names
    """
    return _PROBE_TOKEN.sub(lambda m: PROBE_RENAMES[m.group(0)], text)


def migrate_probes(preview: bool) -> None:
    """
    Rename the CORE test components in the stored artifacts and the ledger, then rebuild its index.

    Args:
        preview: List what would change and write nothing
    """
    file_logging = AppConfigManager().get_file_logging_config_object()
    runs_root = Path(file_logging.run_index).parent
    artifacts = 0
    for artifact in runs_root.rglob('io/*.json'):
        text = artifact.read_text(encoding='utf-8')
        new_text = _rename_probes(text)
        if new_text == text:
            continue
        json.loads(new_text)
        artifacts += 1
        if not preview:
            artifact.write_text(new_text, encoding='utf-8')
    print(f'  {"would rewrite" if preview else "rewrote"} {artifacts} report artifact(s)')

    index = RunLedgerIndex(runs_root / 'ledger', LEDGER_COLUMNS)
    touched = 0
    for fragment in index.fragments():
        frame = pd.read_parquet(fragment)
        renamed = frame.copy()
        for column in renamed.columns:
            if pd.api.types.is_string_dtype(renamed[column]) or renamed[column].dtype == object:
                renamed[column] = renamed[column].map(
                    lambda v: _rename_probes(v) if isinstance(v, str) else v)
        if renamed.equals(frame):
            continue
        touched += 1
        if not preview:
            temporary = fragment.with_suffix('.parquet.tmp')
            renamed.to_parquet(temporary, index=False)
            temporary.replace(fragment)
    print(f'  {"would rewrite" if preview else "rewrote"} {touched} ledger fragment(s)')
    if touched and not preview:
        print(f'  rebuilt the ledger index ({index.rebuild()} rows)')


def _dicts_with_key(node, key: str, path: Tuple[str, ...] = ()) -> List[Tuple[str, ...]]:
    """
    Where in a parsed JSON document a dict carries one key.

    Args:
        node: A parsed JSON value
        key: The key to look for
        path: The keys leading to `node`

    Returns:
        The path of every dict holding the key
    """
    found: List[Tuple[str, ...]] = []
    if isinstance(node, dict):
        if key in node:
            found.append(path)
        for name, value in node.items():
            found.extend(_dicts_with_key(value, key, path + (name,)))
    elif isinstance(node, list):
        for value in node:
            found.extend(_dicts_with_key(value, key, path))
    return found


def _renamed_stress_sources(path: Path) -> Optional[str]:
    """
    The file's text with every stress event's `data_source` renamed, checked by parsing it back.

    Args:
        path: A JSON file

    Returns:
        The new text when the file carries a stress event's `data_source`, else None
    """
    try:
        text = path.read_text(encoding='utf-8')
        data = json.loads(text)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    holders = _dicts_with_key(data, 'data_source')
    if not holders:
        return None
    if any(p[-2:] != ('stale_data_stress', 'events') for p in holders) or \
            len(_STRESS_SOURCE_KEY.findall(text)) != len(holders):
        raise ValueError(f'{path}: a "data_source" sits outside a stress event — rename it by hand')
    new_text = _STRESS_SOURCE_KEY.sub(r'"stale_data_source"\1', text)
    if _dicts_with_key(json.loads(new_text), 'data_source'):
        raise ValueError(f'{path}: a "data_source" survived the rewrite — check by hand')
    return new_text


def _renamed_artifact(path: Path) -> Optional[str]:
    """
    One stored report artifact's text with the data-broker keys renamed, checked by parsing it back.

    Args:
        path: A portfolio.json or scenario_details.json artifact

    Returns:
        The new text when the artifact carries a retired key, else None
    """
    text = path.read_text(encoding='utf-8')
    renames = _ARTIFACT_KEY_RENAMES[path.name]
    new_text = text
    for old, new in renames.items():
        new_text = re.sub(rf'"{old}"(\s*:)', rf'"{new}"\1', new_text)
    if path.name == 'scenario_details.json':
        # The roll-up's rows and the `keys` declaration name the broker as `broker_type`; the
        # rows are the only place that key sits in this artifact.
        new_text = new_text.replace('"broker_type"', '"data_broker_type"')
    if new_text == text:
        return None
    data = json.loads(new_text)
    for old in list(renames) + ['broker_type']:
        if _dicts_with_key(data, old):
            raise ValueError(f'{path}: "{old}" survived the rewrite — check by hand')
    return new_text


def migrate_data_brokers(preview: bool) -> None:
    """
    Split the two meanings of `data_source` in the configurations and the stored artifacts.

    Args:
        preview: List what would change and write nothing
    """
    configs = 0
    for path in _config_files():
        new_text = _renamed_stress_sources(path)
        if new_text is None:
            continue
        configs += 1
        tracked = path.parts[0] in ('configs', 'tests')
        marker = '' if tracked else '  [your workspace]'
        print(f'  {"would rewrite" if preview else "rewrote"} stress keys {path}{marker}')
        if not preview:
            path.write_text(new_text, encoding='utf-8')
    print(f'  {configs} configuration file(s)')

    runs_root = Path(AppConfigManager().get_file_logging_config_object().run_index).parent
    artifacts = 0
    for name in _ARTIFACT_KEY_RENAMES:
        for artifact in sorted(runs_root.rglob(f'io/{name}')):
            new_text = _renamed_artifact(artifact)
            if new_text is None:
                continue
            artifacts += 1
            if not preview:
                artifact.write_text(new_text, encoding='utf-8')
    print(f'  {"would rewrite" if preview else "rewrote"} {artifacts} report artifact(s)')


def main() -> None:
    """Parse arguments and migrate."""
    parser = argparse.ArgumentParser(description='One-time vocabulary migration (2026-09-28)')
    parser.add_argument('--preview', action='store_true',
                        help='List what would change and write nothing')
    parser.add_argument('--only', choices=['configs', 'runs', 'carry_over', 'probes', 'data_brokers'],
                        default=None,
                        help='Run one part only')
    args = parser.parse_args()
    if args.only in (None, 'configs'):
        print('\nConfiguration files')
        count = migrate_configs(args.preview)
        print(f'  {count} file(s)')
    if args.only in (None, 'runs'):
        print('\nRun tree and ledger')
        migrate_runs(args.preview)
    if args.only in (None, 'carry_over'):
        print('\nCold-start carry-over')
        migrate_carry_over(args.preview)
    if args.only in (None, 'probes'):
        print('\nRenamed test probes')
        migrate_probes(args.preview)
    if args.only in (None, 'data_brokers'):
        print('\nThe two meanings of data_source')
        migrate_data_brokers(args.preview)
    print()


if __name__ == '__main__':
    main()
