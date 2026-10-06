"""
Prepares a re-import of chosen tick files from the zipped raw archive, and proves afterwards that
the import rewrote every one of them.

Why it exists: a change to what the importer EXTRACTS from a raw file reaches the data already
imported only through a re-import, and the steps around one — choosing the members, unpacking them
into the importer's inbox, checking the import against the archive, removing the copies the import
leaves behind — must not be programmed anew each time. Built for #562 (the MT5 server clock);
nothing imports it, and it has no entry point in python/cli/ because a re-import is meant to stay
rare.

Phases, and which of them write:
  plan     reads only. What a scope selects and from which zip, how much disk it needs, and
           whether extract would be allowed to start.
  extract  writes. Streams each selected zip member into data/raw/ as <name>.part and renames it
           once the stream has ended (zipfile checks the member's CRC there); MOVES a selected
           loose source there. Beside them it writes a manifest: the scope, the members, the
           tick index rows they had before, and when each one was extracted.
  import   the operator's, not this tool's: python python/cli/data_index_cli.py import --override
  verify   reads only. Checks count, names, inbox and rewritten against the manifest; exit
           code 0 only when every one passes.
  clean    writes. Re-runs verify and refuses unless it is green. Then deletes each loose copy the
           import moved to data/finished/ that is SHA-256-equal to its archived member, keeps one
           that differs (it awaits archiving), and removes the manifest.

Selection: by the MEMBER's own name (SYMBOL_YYYYMMDD_HHMMSS_ticks.json), never by the zip's name.
A batch zip is named after one of its members and can hold another broker's files. The broker type
comes from the tick index row whose source_file is the member; for a member the index does not
know, from the member's own header (broker_type, or the older data_collector), matched exactly
against the configured broker types. A member neither answers for is reported and never selected,
and the broker is never inferred from the shape of the symbol. A window selects by the overlap of
the member's index row (start..end as stored); a member the index does not know falls back to the
date and time in its own name. Every *.zip directly under data/finished/Archives/ belongs to the
archive, and so does a loose data/finished/*_ticks.json whose name is in no zip: that is a source
the importer moved there before its batch was archived, not a copy.

Why count and names are not enough: the importer refuses a file it judges defective BEFORE it
deletes the file's old parquet, so a refused file keeps its old parquet and its index row. The
archive and the index still hold the same number of files under the same names. verify therefore
also checks that no selected file is left in data/raw/ (inbox) and that the parquet of every
selected file was written after the file was extracted (rewritten, read from the processed_at
stamp in the parquet footer).

It never writes into an archive or unpacks one whole, never touches a signal archive, never runs
the import, and never deletes a file that is not SHA-256-equal to its archived member or that is
in no zip.

Recorded runs:
  2026-10-05 · #562 (MT5 server clock) · scope mt5, window 2025-11-02..2026-03-07 · 1,640 members
    from one zip, 30.57 GB · extract 228 s · the arrival-time migration on the inbox 65 min ·
    import --override 33 min (1,640 files, 79,417,157 ticks, 0 refused) · verify: count 5,005 =
    5,005, names both ways, inbox 0, rewritten 1,640 of 1,640 · every file shifted by +1 h, no
    tick count changed

Usage:
    python python/experiments/raw_archive_reimport/raw_archive_reimport.py plan --broker mt5
    python python/experiments/raw_archive_reimport/raw_archive_reimport.py plan --broker mt5 \\
        --window 2025-11-02..2026-03-07
    python python/experiments/raw_archive_reimport/raw_archive_reimport.py extract --broker mt5 \\
        --symbols EURUSD,GBPUSD --window 2025-11-02..2026-03-07
    python python/cli/data_index_cli.py import --override
    python python/experiments/raw_archive_reimport/raw_archive_reimport.py verify
    python python/experiments/raw_archive_reimport/raw_archive_reimport.py clean

A window is START..END in UTC; a bare date as END covers that whole day, and either side may be
left empty. --symbols is a comma-separated list, matched exactly.
"""

import argparse
import json
import os
import re
import shutil
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from itertools import groupby
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

import pandas as pd
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from python.configuration.import_config_manager import ImportConfigManager
from python.configuration.market_config_manager import MarketConfigManager
from python.experiments.raw_archive_reimport.raw_archive_catalog import (
    TICK_INDEX_COLUMNS,
    RawArchiveCatalog,
)
from python.experiments.raw_archive_reimport.raw_archive_reimport_types import (
    ArchiveMember,
    BrokerSource,
    MemberOrigin,
    ReimportCheck,
    ReimportCheckId,
    ReimportManifest,
    ReimportScope,
    TickIndexRow,
)
from python.framework.store.abstract_store_index import store_index_filename
from python.framework.types.store_types import StoreId
from python.framework.utils.time_utils import ensure_utc_aware, parse_datetime

TOOL_VERSION = '1.0'

# The manifest lies in the importer's inbox: the importer reads only *_ticks.json, and the file
# leaves with the run, the way the inbox empties itself.
MANIFEST_FILE_NAME = 'raw_archive_reimport_manifest.json'

# The archive lives beside the files the importer moved to the finished directory.
ARCHIVES_DIR_NAME = 'Archives'

_PART_SUFFIX = '.part'
_TICKS_GLOB = '*_ticks.json'
_COPY_CHUNK_BYTES = 1 << 20

# Free disk extract demands, as a multiple of the bytes it is about to write.
_DISK_HEADROOM = 1.1

_DATE_ONLY = re.compile(r'^\d{4}-\d{2}-\d{2}$')
_TIME_FORMAT = '%Y-%m-%d %H:%M:%S'

# How the tool is run, for the next steps it prints.
_TOOL_PATH = 'python/experiments/raw_archive_reimport/raw_archive_reimport.py'

# How many example names a summary line shows before it says how many more there are.
_EXAMPLES_SHOWN = 5

_PASS_MARK = '✓'
_FAIL_MARK = '✗'


class RawArchiveReimport:
    """
    The phases of a re-import from the raw archive: plan, extract, verify, clean.

    Each phase prints what it did or found and returns the process exit code. The import between
    extract and verify is the operator's, so this class never runs it.
    """

    def __init__(self, catalog: RawArchiveCatalog, raw_dir: Path, manifest_path: Path) -> None:
        """
        Bind the phases to an archive, an inbox and the place of the manifest.

        Args:
            catalog: The raw archive, resolved against the current tick index
            raw_dir: The importer's inbox
            manifest_path: Where extract records what it selected
        """
        self._catalog = catalog
        self._raw_dir = raw_dir
        self._manifest_path = manifest_path

    # =========================================================================
    # PHASES
    # =========================================================================

    def plan(self, scope: ReimportScope) -> int:
        """
        Show what a scope selects and whether extract may start. Reads only.

        Args:
            scope: The re-import scope

        Returns:
            0, or 1 when the archive itself blocks the scope (a name held twice, an unresolved
            member the scope could cover, an unknown broker type)
        """
        members = self._catalog.select(scope)
        errors = self.get_blocking_errors(scope)
        self._render_plan(scope, members, errors)
        return 1 if errors else 0

    def extract(self, scope: ReimportScope) -> int:
        """
        Put the selected members into the importer's inbox, symbol by symbol, and record them
        in the manifest.

        Args:
            scope: The re-import scope

        Returns:
            0 when everything selected is in the inbox, 1 when extract refused to start
        """
        members = sorted(self._catalog.select(scope),
                         key=lambda member: (member.symbol, member.name))
        refusals = self.get_blocking_errors(scope)
        if not members:
            refusals.append('nothing is selected')
        refusals.extend(line for passed, line in self._preflight(members) if not passed)

        print(f'RAW ARCHIVE RE-IMPORT — extract (writes) · tool {TOOL_VERSION}')
        print(f'  scope      {self._describe_scope(scope)}')
        if refusals:
            print('  refused — nothing was written:')
            for line in refusals:
                print(f'    {_FAIL_MARK} {line}')
            return 1

        manifest = ReimportManifest(
            tool_version=TOOL_VERSION, created_at=datetime.now(timezone.utc), scope=scope,
            members=members, before=self._snapshot_index(members, scope), extracted_at={})
        self._raw_dir.mkdir(parents=True, exist_ok=True)
        self._write_manifest(manifest)
        print(f'  manifest   {self._manifest_path}')

        started = time.monotonic()
        try:
            for symbol, group in groupby(members, key=lambda member: member.symbol):
                symbol_members = list(group)
                symbol_started = time.monotonic()
                for member in symbol_members:
                    self._extract_member(member)
                    manifest.extracted_at[member.name] = datetime.now(timezone.utc)
                # Recorded per symbol rather than per member: a manifest rewritten after every
                # file would cost more than the files on this mount.
                self._write_manifest(manifest)
                symbol_bytes = sum(member.size_bytes for member in symbol_members)
                print(f'  {symbol:<12} {len(symbol_members):>7,} files  '
                      f'{_format_bytes(symbol_bytes):>11}  '
                      f'{time.monotonic() - symbol_started:7.1f} s')
        finally:
            self._write_manifest(manifest)
            self._catalog.close()

        total_bytes = sum(member.size_bytes for member in members)
        print(f'  extracted  {len(members):,} members · {_format_bytes(total_bytes)} · '
              f'{time.monotonic() - started:.1f} s')
        print()
        print('  next')
        print('    1. for #562, and any importer change that needs one: run its migration on '
              'the inbox now, before the import')
        print('    2. python python/cli/data_index_cli.py import --override')
        print(f'    3. python {_TOOL_PATH} verify')
        print(f'    4. python {_TOOL_PATH} clean')
        return 0

    def verify(self) -> int:
        """
        Check the import against the archive and the manifest. Reads only.

        Returns:
            0 only when every check passes, 1 otherwise or without a manifest
        """
        manifest = self.read_manifest()
        print(f'RAW ARCHIVE RE-IMPORT — verify (reads only) · tool {TOOL_VERSION}')
        if manifest is None:
            print(f'  no manifest at {self._manifest_path} — nothing was extracted to verify')
            return 1
        checks = self.run_checks(manifest)
        self._render_checks(manifest, checks)
        self._render_changes(manifest)
        return 0 if all(check.passed for check in checks) else 1

    def clean(self) -> int:
        """
        Remove the loose copies the import left behind, once verify is green.

        Returns:
            0 when it cleaned, 1 when it refused
        """
        manifest = self.read_manifest()
        print(f'RAW ARCHIVE RE-IMPORT — clean (writes) · tool {TOOL_VERSION}')
        if manifest is None:
            print(f'  no manifest at {self._manifest_path} — nothing to clean')
            return 1
        checks = self.run_checks(manifest)
        if not all(check.passed for check in checks):
            self._render_checks(manifest, checks)
            print('  refused — verify is not green, nothing was deleted')
            return 1

        finished_dir = self._catalog.get_finished_dir()
        removed_bytes = 0
        removed: List[str] = []
        differing: List[str] = []
        sources: List[str] = []
        absent: List[str] = []
        members = sorted(manifest.members, key=lambda member: (member.symbol, member.name))
        try:
            for symbol, group in groupby(members, key=lambda member: member.symbol):
                symbol_started = time.monotonic()
                symbol_removed = 0
                for member in group:
                    loose = finished_dir / member.name
                    if not loose.is_file():
                        absent.append(member.name)
                        continue
                    archived = self._catalog.get_archived_members(member.name)
                    # In no zip: the only copy there is, never a candidate for deletion. The
                    # guard is load-bearing — all() over no archived members would read as proven.
                    if not archived:
                        sources.append(member.name)
                        continue
                    if all(self._catalog.prove_identical(loose, copy_of) for copy_of in archived):
                        size = loose.stat().st_size
                        loose.unlink()
                        removed.append(member.name)
                        removed_bytes += size
                        symbol_removed += 1
                    else:
                        differing.append(member.name)
                print(f'  {symbol:<12} {symbol_removed:>7,} removed  '
                      f'{time.monotonic() - symbol_started:7.1f} s')
        finally:
            self._catalog.close()

        self._manifest_path.unlink()
        print(f'  removed    {len(removed):,} loose copies, SHA-256-equal to their archived member '
              f'({_format_bytes(removed_bytes)})')
        print(f'  kept       {len(differing):,} awaiting archiving — '
              'differs from its archived member')
        for name in differing:
            print(f'               {name}')
        print(f'  kept       {len(sources):,} source files in no zip')
        if absent:
            print(f'  absent     {len(absent):,} selected files have no loose copy in '
                  f'{finished_dir}')
        print(f'  manifest   removed ({self._manifest_path})')
        return 0

    # =========================================================================
    # CHECKS
    # =========================================================================

    def get_blocking_errors(self, scope: ReimportScope) -> List[str]:
        """
        What in the archive itself stops a re-import of this scope.

        Args:
            scope: The re-import scope

        Returns:
            One line per problem; empty when nothing blocks
        """
        errors: List[str] = []
        known = self._catalog.get_known_broker_types()
        if scope.broker_type not in known:
            errors.append(f"unknown broker type '{scope.broker_type}' — configured: "
                          f"{', '.join(known)}")
        for name, held_by in sorted(self._catalog.get_duplicate_names().items()):
            errors.append(f"{name} is held {len(held_by)} times: {', '.join(held_by)}")
        for member in self._catalog.get_unresolved_in_scope(scope):
            errors.append(f'{member.name} ({member.container.name}) — broker type unresolved, '
                          'and the scope could cover it')
        return errors

    def run_checks(self, manifest: ReimportManifest) -> List[ReimportCheck]:
        """
        The checks of verify, in their order: count, names, inbox, rewritten.

        Args:
            manifest: What extract recorded

        Returns:
            The checks
        """
        count, names = self._check_count_and_names(manifest)
        return [count, names, self._check_inbox(manifest), self._check_rewritten(manifest)]

    def read_manifest(self) -> Optional[ReimportManifest]:
        """
        Read the manifest extract wrote.

        Returns:
            The manifest, or None when there is none
        """
        if not self._manifest_path.is_file():
            return None
        data = json.loads(self._manifest_path.read_text(encoding='utf-8'))
        return ReimportManifest(
            tool_version=str(data['tool_version']),
            created_at=_from_iso(data['created_at']),
            scope=_scope_from_json(data['scope']),
            members=[_member_from_json(entry) for entry in data['members']],
            before={name: tuple(_row_from_json(row) for row in rows)
                    for name, rows in data['before'].items()},
            extracted_at={name: _from_iso(stamp) for name, stamp in data['extracted_at'].items()})

    def _preflight(self, members: List[ArchiveMember]) -> List[Tuple[bool, str]]:
        """
        What extract checks before its first write.

        Args:
            members: The selected members

        Returns:
            (passed, line) per check
        """
        results: List[Tuple[bool, str]] = []

        # A tick file already in the inbox could not be told apart from an extracted one, and
        # the import would take it along. A .part is an extraction that was interrupted.
        leftovers = self._inbox_leftovers()
        if leftovers:
            results.append((False, f'{self._raw_dir} holds {len(leftovers):,} tick files or '
                                   'partial extractions that could not be attributed: '
                                   f'{_examples(leftovers)}'))
        else:
            results.append((True, f'{self._raw_dir} holds no {_TICKS_GLOB} and no '
                                  f'{_TICKS_GLOB}{_PART_SUFFIX}'))

        if self._manifest_path.exists():
            results.append((False, f'a manifest exists at {self._manifest_path} — a re-import is '
                                   'in progress; finish it with verify and clean'))
        else:
            results.append((True, f'no manifest at {self._manifest_path}'))

        # The import moves every file it took to the finished directory and replaces whatever
        # lies there under the same name. A loose copy that differs from its member would be lost.
        finished_dir = self._catalog.get_finished_dir()
        lying_loose = [member.name for member in members
                       if member.origin is MemberOrigin.ZIP
                       and (finished_dir / member.name).exists()]
        if lying_loose:
            results.append((False, f'{len(lying_loose):,} selected members already lie loose in '
                                   f'{finished_dir}, and the import would replace them — archive '
                                   f'or remove them first: {_examples(lying_loose)}'))
        else:
            results.append((True, f'no selected member lies loose in {finished_dir}'))

        needed = sum(member.size_bytes for member in members)
        free = shutil.disk_usage(self._existing_ancestor(self._raw_dir)).free
        if free >= _DISK_HEADROOM * needed:
            results.append((True, f'free {_format_bytes(free)} ≥ {_DISK_HEADROOM} × '
                                  f'{_format_bytes(needed)}'))
        else:
            results.append((False, f'free {_format_bytes(free)} < {_DISK_HEADROOM} × '
                                   f'{_format_bytes(needed)} — select fewer symbols per batch'))
        return results

    def _check_count_and_names(
            self, manifest: ReimportManifest) -> Tuple[ReimportCheck, ReimportCheck]:
        """
        Compare archive sources and index rows over the (broker, symbols) scope, whatever the
        window: first how many, then which.

        Args:
            manifest: What extract recorded

        Returns:
            (count, names)
        """
        scope = manifest.scope
        archive_names: Set[str] = {
            member.name for member in self._catalog.get_members_in_broker_scope(scope)}
        # A loose source the import refused still lies in the inbox, out of the catalog's sight;
        # it remains a source of the archive.
        archive_names.update(member.name for member in manifest.members
                             if member.origin is MemberOrigin.LOOSE)
        index_rows = self._catalog.get_index_rows_in_scope(scope)
        index_names = {row.source_file for row in index_rows}

        count_matches = len(index_rows) == len(archive_names)
        count = ReimportCheck(
            check_id=ReimportCheckId.COUNT, passed=count_matches,
            detail=f"index {len(index_rows):,} {'=' if count_matches else '≠'} "
                   f'archive {len(archive_names):,}',
            offenders=())

        not_indexed = sorted(archive_names - index_names)
        not_archived = sorted(index_names - archive_names)
        offenders = tuple([f'{name} — in the archive, not in the index' for name in not_indexed]
                          + [f'{name} — in the index, in no archive' for name in not_archived])
        names = ReimportCheck(
            check_id=ReimportCheckId.NAMES, passed=not offenders,
            detail='both directions' if not offenders else
            f'{len(not_indexed):,} not in the index · {len(not_archived):,} in no archive',
            offenders=offenders)
        return count, names

    def _check_inbox(self, manifest: ReimportManifest) -> ReimportCheck:
        """
        No selected file is left in the inbox: a file the importer refused stays there, and so
        does every file when the import ran without --override.

        Args:
            manifest: What extract recorded

        Returns:
            The check
        """
        left = [member.name for member in manifest.members
                if (self._raw_dir / member.name).exists()
                or (self._raw_dir / f'{member.name}{_PART_SUFFIX}').exists()]
        return ReimportCheck(
            check_id=ReimportCheckId.INBOX, passed=not left,
            detail=f'{len(left):,} of {len(manifest.members):,} left in {self._raw_dir}',
            offenders=tuple(left))

    def _check_rewritten(self, manifest: ReimportManifest) -> ReimportCheck:
        """
        Every selected file's parquet was written after the file was extracted. A refused file
        keeps its old parquet, which the count and the names cannot tell from a new one.

        Args:
            manifest: What extract recorded

        Returns:
            The check
        """
        offenders: List[str] = []
        for member in manifest.members:
            problem = self._rewrite_problem(member, manifest)
            if problem is not None:
                offenders.append(f'{member.name} — {problem}')
        written = len(manifest.members) - len(offenders)
        return ReimportCheck(
            check_id=ReimportCheckId.REWRITTEN, passed=not offenders,
            detail=f'{written:,} of {len(manifest.members):,} written after extraction',
            offenders=tuple(offenders))

    def _rewrite_problem(self, member: ArchiveMember, manifest: ReimportManifest) -> Optional[str]:
        """
        Why one member's parquet does not prove a rewrite, read from the parquet footer.

        Args:
            member: The selected member
            manifest: What extract recorded

        Returns:
            The reason, or None when the parquet was written after the extraction
        """
        extracted_at = manifest.extracted_at.get(member.name)
        if extracted_at is None:
            return 'never extracted'
        rows = self._catalog.get_index_rows(member.name, manifest.scope.broker_type)
        if not rows:
            return 'not in the index'
        for row in rows:
            parquet = Path(row.parquet_path)
            if not parquet.is_file():
                return f'its parquet {parquet.name} is missing'
            try:
                footer = pq.read_schema(parquet).metadata or {}
            except (OSError, ValueError) as error:
                return f'the footer of {parquet.name} is unreadable: {error}'
            source_file = footer.get(b'source_file', b'').decode('utf-8')
            if source_file != member.name:
                return f"{parquet.name} names '{source_file}' as its source"
            stamp = footer.get(b'processed_at', b'').decode('utf-8')
            if not stamp:
                return f'{parquet.name} carries no processed_at'
            try:
                processed_at = parse_datetime(stamp)
            except (ValueError, OverflowError):
                return f"{parquet.name} carries an unreadable processed_at '{stamp}'"
            if processed_at < extracted_at:
                return (f'{parquet.name} was written {processed_at.strftime(_TIME_FORMAT)} UTC, '
                        f'before its extraction at {extracted_at.strftime(_TIME_FORMAT)} UTC')
        return None

    # =========================================================================
    # WRITING
    # =========================================================================

    def _extract_member(self, member: ArchiveMember) -> None:
        """
        Put one member into the inbox. A zip member is streamed to '<name>.part' and renamed
        once the stream has ended, which is where zipfile checks its CRC; a loose source is
        moved, never copied.

        Args:
            member: The member
        """
        target = self._raw_dir / member.name
        if member.origin is MemberOrigin.LOOSE:
            os.replace(member.container, target)
            return
        partial = self._raw_dir / f'{member.name}{_PART_SUFFIX}'
        try:
            with self._catalog.open_member(member) as source, open(partial, 'wb') as sink:
                shutil.copyfileobj(source, sink, _COPY_CHUNK_BYTES)
        except BaseException:
            partial.unlink(missing_ok=True)
            raise
        os.replace(partial, target)

    def _snapshot_index(self, members: List[ArchiveMember],
                        scope: ReimportScope) -> Dict[str, Tuple[TickIndexRow, ...]]:
        """
        The tick index rows the selected names have before anything is written.

        Args:
            members: The selected members
            scope: The re-import scope

        Returns:
            Name -> its rows; an empty tuple for a name the index does not know
        """
        return {member.name: tuple(self._catalog.get_index_rows(member.name, scope.broker_type))
                for member in members}

    def _write_manifest(self, manifest: ReimportManifest) -> None:
        """
        Write the manifest atomically: a temporary file beside it, then a rename.

        Args:
            manifest: The manifest
        """
        data = {
            'tool_version': manifest.tool_version,
            'created_at': _to_iso(manifest.created_at),
            'scope': _scope_to_json(manifest.scope),
            'members': [_member_to_json(member) for member in manifest.members],
            'before': {name: [_row_to_json(row) for row in rows]
                       for name, rows in manifest.before.items()},
            'extracted_at': {name: _to_iso(stamp) for name, stamp in manifest.extracted_at.items()},
        }
        temporary = self._manifest_path.with_name(f'{self._manifest_path.stem}.tmp.json')
        temporary.write_text(json.dumps(data, indent=1), encoding='utf-8')
        os.replace(temporary, self._manifest_path)

    # =========================================================================
    # RENDERING
    # =========================================================================

    def _render_plan(self, scope: ReimportScope, members: List[ArchiveMember],
                     errors: List[str]) -> None:
        """
        Print the plan.

        Args:
            scope: The re-import scope
            members: The selected members
            errors: What blocks the scope
        """
        catalog = self._catalog
        all_members = catalog.get_members()
        archives = catalog.get_archives()
        ignored = catalog.get_ignored_entries()
        zip_members = [member for member in all_members if member.origin is MemberOrigin.ZIP]
        loose_sources = [member for member in all_members if member.origin is MemberOrigin.LOOSE]
        resolved_by = Counter(member.broker_source for member in all_members)

        print(f'RAW ARCHIVE RE-IMPORT — plan (reads only) · tool {TOOL_VERSION}')
        print(f'  archives   {catalog.get_archives_dir()}  {len(archives):,} zips · '
              f'{len(zip_members):,} members · {len(ignored):,} entries ignored')
        if ignored:
            print(f'             ignored: {_examples(ignored)}')
        skipped = catalog.get_skipped_archives()
        if skipped:
            skipped_names = [path.name for path in skipped]
            print(f'             not read (signal archives): {_examples(skipped_names)}')
        print(f'  loose      {catalog.get_finished_dir()}  {len(loose_sources):,} source files in '
              f'no zip · {len(catalog.get_loose_copies()):,} copies of an archived member')
        print(f'  broker     tick index {resolved_by[BrokerSource.TICK_INDEX]:,} · member header '
              f'{resolved_by[BrokerSource.MEMBER_HEADER]:,} · unresolved '
              f'{resolved_by[BrokerSource.UNRESOLVED]:,}')
        print(f'  scope      {self._describe_scope(scope)}')
        selected_line = f'  selected   {len(members):,} members'
        if scope.window_start is not None or scope.window_end is not None:
            by_row = sum(1 for member in members if member.index_start is not None)
            selected_line += (f' · window decided by the index row for {by_row:,}, '
                              f'by the file name for {len(members) - by_row:,}')
        print(selected_line)

        print()
        print(f"  {'zip':<44} {'members':>9} {'selected':>9}")
        selected_by_container = Counter(member.container for member in members
                                        if member.origin is MemberOrigin.ZIP)
        for archive in archives:
            held = [member for member in zip_members if member.container == archive]
            selected_here = selected_by_container[archive]
            print(f'  {archive.name:<44} {len(held):>9,} {selected_here:>9,}'
                  f'{self._name_hint(archive, held, scope, selected_here)}')
        selected_loose = sum(1 for member in members if member.origin is MemberOrigin.LOOSE)
        print(f"  {'loose sources':<44} {len(loose_sources):>9,} {selected_loose:>9,}")

        print()
        print(f"  {'symbol':<12} {'files':>9} {'uncompressed':>14}")
        files_by_symbol = Counter(member.symbol for member in members)
        bytes_by_symbol: Counter[str] = Counter()
        for member in members:
            bytes_by_symbol[member.symbol] += member.size_bytes
        for symbol in sorted(files_by_symbol):
            print(f'  {symbol:<12} {files_by_symbol[symbol]:>9,} '
                  f'{_format_bytes(bytes_by_symbol[symbol]):>14}')
        print(f"  {'total':<12} {len(members):>9,} "
              f'{_format_bytes(sum(bytes_by_symbol.values())):>14}')

        print()
        print('  preflight — what extract checks before it writes')
        for passed, line in self._preflight(members):
            print(f'    {_PASS_MARK if passed else _FAIL_MARK} {line}')
        print()
        if errors:
            print('  errors — plan exits 1 and extract refuses')
            for line in errors:
                print(f'    {_FAIL_MARK} {line}')
        else:
            print('  errors     none')

    def _render_checks(self, manifest: ReimportManifest, checks: List[ReimportCheck]) -> None:
        """
        Print the manifest's header line and the checks, with the names that failed.

        Args:
            manifest: What extract recorded
            checks: The checks
        """
        print(f'  manifest   {manifest.created_at.strftime(_TIME_FORMAT)} UTC · '
              f'tool {manifest.tool_version} · {self._describe_scope(manifest.scope)} · '
              f'{len(manifest.members):,} selected')
        if manifest.tool_version != TOOL_VERSION:
            print(f'             written by tool {manifest.tool_version}, read by {TOOL_VERSION}')
        for check in checks:
            mark = _PASS_MARK if check.passed else _FAIL_MARK
            print(f'  {check.check_id.value:<10} {check.detail:<60} {mark}')
            for offender in check.offenders:
                print(f'               {offender}')

    def _render_changes(self, manifest: ReimportManifest) -> None:
        """
        Print what the import changed in the tick index, per selected member: its start, end
        and tick count before and after. The shifts and `unchanged` describe start and end; a
        changed tick count is counted on its own. A member whose row did not change at all is
        only counted, every other one is listed.

        Args:
            manifest: What extract recorded
        """
        shifted: Counter[timedelta] = Counter()
        reshaped = 0
        unchanged = 0
        ticks_changed = 0
        not_comparable = 0
        lines: List[str] = []
        broker_type = manifest.scope.broker_type
        for member in sorted(manifest.members, key=lambda member: (member.symbol, member.name)):
            before = manifest.before.get(member.name, ())
            after = self._catalog.get_index_rows(member.name, broker_type)
            if len(before) != 1 or len(after) != 1:
                not_comparable += 1
                lines.append(f'    {member.name}  not comparable: {len(before)} index rows '
                             f'before, {len(after)} after')
                continue
            old, new = before[0], after[0]
            if old.start_time is None or old.end_time is None \
                    or new.start_time is None or new.end_time is None:
                not_comparable += 1
                lines.append(f'    {member.name}  not comparable: a start or end is missing')
                continue
            start_shift = new.start_time - old.start_time
            end_shift = new.end_time - old.end_time
            tick_count_moved = new.tick_count != old.tick_count
            ticks_changed += 1 if tick_count_moved else 0
            if start_shift == end_shift == timedelta(0):
                unchanged += 1
                if not tick_count_moved:
                    continue
            elif start_shift == end_shift:
                shifted[start_shift] += 1
            else:
                reshaped += 1
            lines.append(f'    {member.name}  start {old.start_time.strftime(_TIME_FORMAT)} → '
                         f'{new.start_time.strftime(_TIME_FORMAT)}  end '
                         f'{old.end_time.strftime(_TIME_FORMAT)} → '
                         f'{new.end_time.strftime(_TIME_FORMAT)}  ticks '
                         f'{old.tick_count:,} → {new.tick_count:,}')

        parts = [f'{count:,} files shifted by {_format_shift(shift)}'
                 for shift, count in sorted(shifted.items())]
        if reshaped:
            parts.append(f'{reshaped:,} with start and end moved apart')
        parts.append(f'{unchanged:,} unchanged')
        parts.append(f'{ticks_changed:,} tick counts changed')
        if not_comparable:
            parts.append(f'{not_comparable:,} not comparable')
        print(f"  change     {' · '.join(parts)}")
        for line in lines:
            print(line)

    @staticmethod
    def _name_hint(archive: Path, held: List[ArchiveMember], scope: ReimportScope,
                   selected_here: int) -> str:
        """
        A warning when a zip's own name points at a member of another broker than the scope's,
        while the scope selects from it — the case where selecting by the zip's name would err.

        Args:
            archive: The zip
            held: Its members
            scope: The re-import scope
            selected_here: How many of its members are selected

        Returns:
            The hint, or an empty string
        """
        if not selected_here:
            return ''
        named_after = next((member for member in held if member.name == f'{archive.stem}.json'),
                           None)
        if named_after is None or named_after.broker_type in (None, scope.broker_type):
            return ''
        return f'   ← named after a {named_after.broker_type} member'

    @staticmethod
    def _describe_scope(scope: ReimportScope) -> str:
        """
        One line naming a scope.

        Args:
            scope: The re-import scope

        Returns:
            The line
        """
        symbols = ', '.join(scope.symbols) if scope.symbols else 'all symbols'
        if scope.window_start is None and scope.window_end is None:
            window = 'no window'
        else:
            start = scope.window_start.strftime(_TIME_FORMAT) if scope.window_start else '…'
            end = scope.window_end.strftime(_TIME_FORMAT) if scope.window_end else '…'
            window = f'window {start} → {end} UTC'
        return f'{scope.broker_type} · {symbols} · {window}'

    def _inbox_leftovers(self) -> List[str]:
        """
        Tick files and partial extractions already lying in the inbox.

        Returns:
            Their names, sorted
        """
        if not self._raw_dir.is_dir():
            return []
        found = list(self._raw_dir.glob(_TICKS_GLOB))
        found.extend(self._raw_dir.glob(f'{_TICKS_GLOB}{_PART_SUFFIX}'))
        return sorted(path.name for path in found)

    @staticmethod
    def _existing_ancestor(path: Path) -> Path:
        """
        The path itself, or its nearest existing parent, to measure free disk on.

        Args:
            path: A directory that may not exist yet

        Returns:
            An existing directory
        """
        candidate = path.resolve()
        while not candidate.exists():
            candidate = candidate.parent
        return candidate


# =============================================================================
# ARGUMENTS
# =============================================================================


def parse_window(text: str) -> Tuple[Optional[datetime], Optional[datetime]]:
    """
    Read a window written START..END in UTC; a bare date as END covers that whole day.

    Args:
        text: The window, e.g. '2025-11-02..2026-03-08'; either side may be empty

    Returns:
        (start, end), UTC-aware, inclusive; None for an open side
    """
    if '..' not in text:
        raise argparse.ArgumentTypeError(
            f"invalid window '{text}' — write START..END, e.g. 2025-11-02..2026-03-08")
    start_text, end_text = (side.strip() for side in text.split('..', 1))
    start = _parse_window_side(start_text, closes=False) if start_text else None
    end = _parse_window_side(end_text, closes=True) if end_text else None
    if start is None and end is None:
        raise argparse.ArgumentTypeError(f"invalid window '{text}' — both sides are empty")
    if start is not None and end is not None and start > end:
        raise argparse.ArgumentTypeError(f"invalid window '{text}' — it ends before it starts")
    return start, end


def parse_symbols(text: str) -> Tuple[str, ...]:
    """
    Read a comma-separated symbol list.

    Args:
        text: E.g. 'EURUSD,GBPUSD'; empty for every symbol

    Returns:
        The symbols as written, without blanks and repeats
    """
    symbols: List[str] = []
    for part in text.split(','):
        symbol = part.strip()
        if symbol and symbol not in symbols:
            symbols.append(symbol)
    return tuple(symbols)


def _parse_window_side(text: str, closes: bool) -> datetime:
    """
    Read one side of a window.

    Args:
        text: A date or a date and time; a time without a zone is read as UTC
        closes: True for the END side, where a bare date means the end of that day

    Returns:
        The UTC-aware instant
    """
    try:
        instant = parse_datetime(text)
    except (ValueError, OverflowError):
        raise argparse.ArgumentTypeError(f"invalid window side '{text}'")
    if closes and _DATE_ONLY.match(text):
        instant = instant + timedelta(days=1) - timedelta(microseconds=1)
    return instant


# =============================================================================
# MANIFEST SERIALIZATION
# =============================================================================


def _to_iso(stamp: Optional[datetime]) -> Optional[str]:
    """
    Args:
        stamp: A UTC-aware instant, or None

    Returns:
        Its ISO form, or None
    """
    return None if stamp is None else stamp.isoformat()


def _from_iso(text: str) -> datetime:
    """
    Args:
        text: An ISO instant

    Returns:
        The UTC-aware instant
    """
    return ensure_utc_aware(datetime.fromisoformat(text))


def _from_optional_iso(text: Optional[str]) -> Optional[datetime]:
    """
    Args:
        text: An ISO instant, or None

    Returns:
        The UTC-aware instant, or None
    """
    return None if text is None else _from_iso(text)


def _scope_to_json(scope: ReimportScope) -> Dict[str, Any]:
    """
    Args:
        scope: The re-import scope

    Returns:
        Its JSON form
    """
    return {
        'broker_type': scope.broker_type,
        'symbols': list(scope.symbols),
        'window_start': _to_iso(scope.window_start),
        'window_end': _to_iso(scope.window_end),
    }


def _scope_from_json(data: Dict[str, Any]) -> ReimportScope:
    """
    Args:
        data: A scope's JSON form

    Returns:
        The scope
    """
    return ReimportScope(
        broker_type=str(data['broker_type']), symbols=tuple(data['symbols']),
        window_start=_from_optional_iso(data['window_start']),
        window_end=_from_optional_iso(data['window_end']))


def _member_to_json(member: ArchiveMember) -> Dict[str, Any]:
    """
    Args:
        member: An archive member

    Returns:
        Its JSON form
    """
    return {
        'name': member.name,
        'symbol': member.symbol,
        'name_time': _to_iso(member.name_time),
        'broker_type': member.broker_type,
        'broker_source': member.broker_source.value,
        'origin': member.origin.value,
        'container': str(member.container),
        'entry_name': member.entry_name,
        'size_bytes': member.size_bytes,
        'crc32': member.crc32,
        'index_start': _to_iso(member.index_start),
        'index_end': _to_iso(member.index_end),
    }


def _member_from_json(data: Dict[str, Any]) -> ArchiveMember:
    """
    Args:
        data: A member's JSON form

    Returns:
        The member
    """
    return ArchiveMember(
        name=str(data['name']), symbol=str(data['symbol']), name_time=_from_iso(data['name_time']),
        broker_type=data['broker_type'], broker_source=BrokerSource(data['broker_source']),
        origin=MemberOrigin(data['origin']), container=Path(data['container']),
        entry_name=str(data['entry_name']), size_bytes=int(data['size_bytes']),
        crc32=data['crc32'], index_start=_from_optional_iso(data['index_start']),
        index_end=_from_optional_iso(data['index_end']))


def _row_to_json(row: TickIndexRow) -> Dict[str, Any]:
    """
    Args:
        row: A tick index row

    Returns:
        Its JSON form
    """
    return {
        'source_file': row.source_file,
        'broker_type': row.broker_type,
        'symbol': row.symbol,
        'parquet_path': row.parquet_path,
        'start_time': _to_iso(row.start_time),
        'end_time': _to_iso(row.end_time),
        'tick_count': row.tick_count,
    }


def _row_from_json(data: Dict[str, Any]) -> TickIndexRow:
    """
    Args:
        data: A row's JSON form

    Returns:
        The row
    """
    return TickIndexRow(
        source_file=str(data['source_file']), broker_type=str(data['broker_type']),
        symbol=str(data['symbol']), parquet_path=str(data['parquet_path']),
        start_time=_from_optional_iso(data['start_time']),
        end_time=_from_optional_iso(data['end_time']), tick_count=int(data['tick_count']))


# =============================================================================
# FORMATTING
# =============================================================================


def _format_bytes(size: int) -> str:
    """
    Args:
        size: A size in bytes

    Returns:
        It in decimal units (GB = 10^9 bytes)
    """
    for unit, factor in (('GB', 1e9), ('MB', 1e6), ('KB', 1e3)):
        if size >= factor:
            return f'{size / factor:,.2f} {unit}'
    return f'{size:,} B'


def _format_shift(shift: timedelta) -> str:
    """
    Args:
        shift: How far a row's start and end moved

    Returns:
        It in whole hours or minutes where it is one, in seconds otherwise
    """
    seconds = shift.total_seconds()
    if seconds % 3600 == 0:
        return f'{int(seconds // 3600):+d} h'
    if seconds % 60 == 0:
        return f'{int(seconds // 60):+d} min'
    return f'{seconds:+,.3f} s'


def _examples(names: List[str]) -> str:
    """
    Args:
        names: Names to show

    Returns:
        The first few, and how many more there are
    """
    shown = ', '.join(names[:_EXAMPLES_SHOWN])
    hidden = len(names) - _EXAMPLES_SHOWN
    return f'{shown} … and {hidden:,} more' if hidden > 0 else shown


# =============================================================================
# ENTRY POINT
# =============================================================================


def _build_from_config() -> RawArchiveReimport:
    """
    Build the tool over the configured import paths and the current tick index.

    Returns:
        The tool
    """
    import_config = ImportConfigManager()
    raw_dir = Path(import_config.get_data_raw_path())
    finished_dir = Path(import_config.get_data_finished_path())
    index_path = Path(import_config.get_import_output_path()) / store_index_filename(StoreId.TICKS)
    tick_index = (pd.read_parquet(index_path, columns=list(TICK_INDEX_COLUMNS))
                  if index_path.is_file() else pd.DataFrame())
    catalog = RawArchiveCatalog(
        archives_dir=finished_dir / ARCHIVES_DIR_NAME, finished_dir=finished_dir,
        tick_index=tick_index, known_broker_types=MarketConfigManager().get_all_broker_types())
    return RawArchiveReimport(catalog=catalog, raw_dir=raw_dir,
                              manifest_path=raw_dir / MANIFEST_FILE_NAME)


def main() -> int:
    """
    Parse the command and run its phase.

    Returns:
        The process exit code
    """
    parser = argparse.ArgumentParser(
        description='Re-import chosen tick files from the zipped raw archive, and prove '
                    'afterwards that the import rewrote every one of them.')
    commands = parser.add_subparsers(dest='command', required=True)
    for command, help_text in (
            ('plan', 'show what a scope selects and whether extract may start (reads only)'),
            ('extract', 'put the selected members into the importer inbox (writes)')):
        sub = commands.add_parser(command, help=help_text)
        sub.add_argument('--broker', required=True,
                         help='broker type exactly as configured, e.g. mt5')
        sub.add_argument('--symbols', default='',
                         help='comma-separated symbols, matched exactly; every symbol when omitted')
        sub.add_argument('--window', type=parse_window, default=(None, None),
                         help='START..END in UTC, e.g. 2025-11-02..2026-03-08 (END date inclusive)')
    commands.add_parser('verify', help='check the import against the archive (reads only)')
    commands.add_parser(
        'clean', help='remove the loose copies proven equal to their member (writes)')
    args = parser.parse_args()

    tool = _build_from_config()
    if args.command in ('plan', 'extract'):
        window_start, window_end = args.window
        scope = ReimportScope(broker_type=args.broker, symbols=parse_symbols(args.symbols),
                              window_start=window_start, window_end=window_end)
        return tool.plan(scope) if args.command == 'plan' else tool.extract(scope)
    return tool.verify() if args.command == 'verify' else tool.clean()


if __name__ == '__main__':
    sys.exit(main())
