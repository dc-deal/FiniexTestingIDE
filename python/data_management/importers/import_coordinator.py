"""
FiniexTestingIDE - Import Coordinator
One import over every archive: ticks first, then signals, then the shared caches (#175).

The coordinator owns the order and the reporting; the importers keep their own logic and are
called step by step. Before anything is written, the one inbox is scanned, so the screen shows
every step with its count and an estimate from the start. Every line the import logs also goes
to its own file, logs/imports/import_<import_id>.log, which the summary names at the end.
"""

import json
import queue
import re
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable, Dict, List, Optional, Tuple, Union

import pandas as pd
import pyarrow.parquet as pq

from python.configuration.app_config_manager import AppConfigManager
from python.configuration.import_config_manager import ImportConfigManager
from python.data_management.importers.bar_importer import (
    BarImporter,
    effective_render_workers,
    is_production_archive,
)
from python.data_management.importers.import_progress_tracker import ImportProgressTracker
from python.data_management.importers.signal_data_importer import SignalDataImporter
from python.data_management.importers.tick_data_importer import TickDataImporter
from python.data_management.importers.import_inbox_conveyor import file_sha256
from python.data_management.importers.package_client import PackageClient, parse_manifest
from python.data_management.importers.import_zip_inbox import ImportZipInbox, manifest_companion
from python.data_management.index.bars_index_manager import BarsIndexManager
from python.data_management.index.signal_index_manager import SignalIndexManager
from python.data_management.index.tick_index_manager import TickIndexManager
from python.framework.activities.activity_registry import ActivityRegistry
from python.framework.constants.run_layout_constants import IMPORT_LOG_SUBDIR
from python.framework.exceptions.data_quality_errors import InboxZipUnpackException
from python.framework.logging.bootstrap_logger import get_global_logger
from python.framework.store.import_timings_store import ImportTimingsStore
from python.framework.types.activity_registry_types import ActivityKind
from python.framework.types.import_types.import_progress_types import (
    ImportOutcome,
    ImportPaths,
    ImportRateUnit,
    ImportSection,
    ImportStepId,
    ImportStepStatus,
    InboxByHand,
    InboxFile,
    InboxScan,
)
from python.framework.types.import_types.import_inbox_types import (
    InboxDisposition,
    InboxFileKind,
    InboxImportOutcome,
    InboxImportResult,
    InboxZipContent,
    InboxZipMember,
    InboxZipSettlement,
)
from python.framework.types.import_types.tick_index_types import (
    TickIndexMethod,
    TickIndexRebuildReason,
    TickIndexRun,
)
from python.framework.types.config_types.package_service_config_types import ActivePackageSource
from python.framework.types.import_types.package_types import (
    PackageFetch,
    PackageFetchOutcome,
    PackageListing,
    PackageManifest,
    PackageServiceResolution,
    PackageServiceState,
    PackageState,
    PackageReceiptOutcome,
)
from python.framework.types.log_level import LogLevel
from python.framework.types.run_origin_types import RunChannel
from python.framework.utils.file_utils import create_directory
from python.framework.utils.interrupt_utils import sigterm_as_interrupt
from python.framework.utils.run_id_utils import mint_run_id
from python.framework.utils.time_utils import format_duration
from python.system.ui.import_live_display import ImportLiveDisplay
from python.system.ui.ui_render_utils import BYTES_PER_MB, format_size

vLog = get_global_logger()

# Why a build of the tick index read every file, in words for the screen and the summary
_INDEX_REBUILD_REASON_TEXTS: Dict[TickIndexRebuildReason, str] = {
    TickIndexRebuildReason.ASKED_FOR: 'asked for',
    TickIndexRebuildReason.NO_INDEX: 'no index yet',
    TickIndexRebuildReason.OLDER_SCHEMA: 'the index was written under an older schema',
    TickIndexRebuildReason.NO_FILE_IDENTITIES: 'the index predates the file identities',
    TickIndexRebuildReason.NEWER_FILES: 'tick files newer than the index',
}

# The collector writes its metadata block first; broker_type sits well inside this many bytes.
_HEAD_BYTES = 8192
_BROKER_TYPE_PATTERN = re.compile(r'"broker_type"\s*:\s*"([^"]+)"')
_FRAME_QUEUE_SIZE = 8

# The exit code of `data_index_cli.py import` per outcome: 130 is the shell's code for Ctrl+C.
IMPORT_EXIT_CODES: Dict[ImportOutcome, int] = {
    ImportOutcome.COMPLETED: 0,
    ImportOutcome.COMPLETED_WITH_ERRORS: 1,
    ImportOutcome.INTERRUPTED: 130,
    ImportOutcome.FAILED: 1,
}


def import_paths_from_config() -> ImportPaths:
    """
    The paths of a production import, from import_config.json and app_config.json.

    Returns:
        ImportPaths
    """
    import_config = ImportConfigManager()
    move_files = import_config.get_move_processed_files()
    file_logging = AppConfigManager().get_file_logging_config_object()
    return ImportPaths(
        inbox=Path(import_config.get_data_raw_path()),
        tick_target=Path(import_config.get_import_output_path()),
        finished=Path(import_config.get_data_finished_path()) if move_files else None,
        unpacking=Path(import_config.get_data_unpacking_path()),
        refused=Path(import_config.get_data_refused_path()) if move_files else None,
        signal_target=Path(import_config.get_signal_import_output_path()),
        log_dir=Path(file_logging.global_log_dir) / IMPORT_LOG_SUBDIR,
        timings_file=Path(import_config.get_import_timings_path()),
    )


class ImportCoordinator:
    """
    Runs one import over the import inbox: each raw file, loose or in a zip, goes to the import
    of its kind — tick exports to the tick archive, signal files to the signal archive.

    Args:
        paths: Where the import reads and writes
        override: Replace existing parquet files instead of skipping them
        only: Import one archive only (TICKS or SIGNALS), None for both; the files of the other
            kind stay in the inbox
        show_display: Ask for the live screen; it still stays off when the config switches it
            off or stdout is not a terminal
        package_services: Every package service as its configuration resolves it; None when the
            import was not asked to fetch (no --fetch)
        client_factory: Builds the client of a switched-on service; the package client when
            omitted, a test hands in its own
    """

    def __init__(
        self,
        paths: ImportPaths,
        override: bool = False,
        only: Optional[ImportSection] = None,
        show_display: bool = True,
        channel: RunChannel = RunChannel.DIRECT,
        package_services: Optional[List[PackageServiceResolution]] = None,
        client_factory: Callable[[ActivePackageSource], PackageClient] = PackageClient,
    ):
        self._paths = paths
        # How the import was started, as its entry point declares it — for its entry in the list
        # of what runs on the machine (#603)
        self._channel = channel
        self._override = override
        self._only = only
        self._import_config = ImportConfigManager()
        display_config = self._import_config.get_display_config()
        self._display_enabled = (show_display and display_config.enabled
                                 and sys.stdout.isatty())
        self._update_interval_s = display_config.update_interval_ms / 1000.0

        # The run-id form — readable UTC start plus a random half: two imports started in one
        # second would otherwise share one log file, and the second would overwrite the first
        self._import_id = mint_run_id(datetime.now(timezone.utc))
        self._log_path = paths.log_dir / f'import_{self._import_id}.log'
        self._timings = ImportTimingsStore(paths.timings_file)
        self._writes_production = is_production_archive(paths.tick_target)

        self._tick_importer: Optional[TickDataImporter] = None
        self._zip_inbox: Optional[ImportZipInbox] = None
        # Each zip of the inbox once the import is through with it, refused ones included
        self._zip_settlements: List[InboxZipSettlement] = []
        # The zips refused or kept in the inbox, counted like the importers' own errors
        self._zip_errors: List[str] = []
        # How the tick index was brought up to date — update or full rebuild — for the summary
        self._tick_index_run: Optional[TickIndexRun] = None
        self._signal_importer: Optional[SignalDataImporter] = None
        self._bar_importer: Optional[BarImporter] = None
        self._tracker: Optional[ImportProgressTracker] = None
        self._frame_queue: Optional[queue.Queue] = None
        self._started = 0.0
        self._scan = InboxScan()
        # What the inbox held at the scan that no package service delivered now
        self._by_hand = InboxByHand()
        # Entries of each counted list the tracker has already seen — what tells a NEW issue
        # apart, per list, so the pinned one is the newest even when lists grow out of order
        self._issues_seen: Dict[str, int] = {}

        # The package services' packages: the services, a client per switched-on service this
        # import fetches from, what the fetch did with each package, their manifests by zip name,
        # how each receipt ended (by service and package), and what the exchange gave to report
        self._package_services = package_services
        self._client_factory = client_factory
        self._package_clients: Dict[str, PackageClient] = {}
        self._package_fetches: List[PackageFetch] = []
        self._package_manifests: Dict[str, PackageManifest] = {}
        self._receipts: Dict[Tuple[str, str], Tuple[PackageReceiptOutcome, Optional[str]]] = {}
        self._package_warnings: List[str] = []
        self._package_errors: List[str] = []

    def get_log_path(self) -> Path:
        """
        The import's own log file.

        Returns:
            Its path
        """
        return self._log_path

    # =========================================================================
    # RUN
    # =========================================================================

    def run(self) -> ImportOutcome:
        """
        Run the whole import.

        A stop (Ctrl+C, SIGTERM) ends it as INTERRUPTED: the step that was running says so,
        the rest are skipped, and an interrupted render still rebuilds the bar index from the
        files that are there. A code crash ends it as FAILED and is raised after the summary.

        Returns:
            How it ended
        """
        # Listed as running for as long as it runs (#603), named by the id its log is named by
        with ActivityRegistry().running(ActivityKind.IMPORT, self._import_id,
                                        channel=self._channel):
            return self._run()

    def _run(self) -> ImportOutcome:
        """
        The import itself, while its activity is listed.

        Returns:
            How it ended
        """
        self._started = time.monotonic()
        if self._display_enabled:
            self._frame_queue = queue.Queue(maxsize=_FRAME_QUEUE_SIZE)
        self._tracker = ImportProgressTracker(
            import_id=self._import_id,
            log_path=str(self._log_path),
            rates=self._timings.load(),
            frame_queue=self._frame_queue,
            update_interval_s=self._update_interval_s,
        )

        outcome = ImportOutcome.FAILED
        crash: Optional[BaseException] = None
        display: Optional[ImportLiveDisplay] = None

        with vLog.tee_to(self._log_path):
            try:
                # A SIGTERM ends the import like Ctrl+C, so the running step says it was
                # interrupted and an interrupted render still rebuilds its index
                with sigterm_as_interrupt():
                    vLog.info(f'📥 Import {self._import_id} — log: {self._log_path}')
                    self._plan_steps()
                    self._tracker.mark_plan_complete()
                    if self._display_enabled:
                        display = ImportLiveDisplay(self._frame_queue, self._update_interval_s)
                        display.start()
                        vLog.set_console_muted(True)
                        self._tracker.start_refresh()
                    self._tracker.publish(force=True)
                    self._run_steps()
                    outcome = (ImportOutcome.COMPLETED_WITH_ERRORS if self._error_count()
                               else ImportOutcome.COMPLETED)
            except KeyboardInterrupt:
                outcome = ImportOutcome.INTERRUPTED
                self._tracker.finish_open_steps(ImportStepStatus.INTERRUPTED)
                vLog.warning(f'⚠️  Import {self._import_id} interrupted')
            except Exception as e:
                outcome = ImportOutcome.FAILED
                crash = e
                self._tracker.finish_open_steps(ImportStepStatus.FAILED)
                vLog.error(f'❌ Import {self._import_id} failed: {e}')
                vLog.error(traceback.format_exc())
            finally:
                self._tracker.stop_refresh()
                self._update_counters()
                self._tracker.set_outcome(outcome)
                if display is not None:
                    display.stop()
                vLog.set_console_muted(False)

            self._print_summary(outcome)
            self._record_timings(outcome)
            vLog.info(f'📝 Full log: {self._log_path}')

        if crash is not None:
            raise crash
        return outcome

    # =========================================================================
    # PLAN
    # =========================================================================

    def _plan_steps(self) -> None:
        """
        Build the importers, scan the inbox, and lay out every step with its count and
        estimate. A section the inbox holds nothing for keeps its steps, marked skipped.

        Returns:
            None
        """
        tracker = self._tracker
        wants_ticks = self._only in (None, ImportSection.TICKS)
        wants_signals = self._only in (None, ImportSection.SIGNALS)

        # The package services' packages land in the import inbox before it is scanned, so the
        # fetch runs ahead of the plan, logged line by line; the screen shows it done once it
        # opens. Each service is reached only for an archive this import writes
        fetch_skipped_because = self._open_package_clients(wants_ticks, wants_signals)
        tracker.add_step(ImportStepId.PACKAGES_FETCH, ImportSection.INBOXES,
                         'Fetch packages', counter_unit='packages')
        if fetch_skipped_because:
            tracker.skip_step(ImportStepId.PACKAGES_FETCH, fetch_skipped_because)
        else:
            self._run_fetch_step()

        tracker.add_step(ImportStepId.SCAN, ImportSection.INBOXES, 'Scan the import inbox',
                         counter_unit='files')
        tracker.start_step(ImportStepId.SCAN)
        renders_bars = wants_ticks and self._import_config.get_auto_render_bars()

        finished_dir = str(self._paths.finished) if self._paths.finished is not None else None
        refused_dir = str(self._paths.refused) if self._paths.refused is not None else None
        # One inbox for both kinds: a zip's members go to the import of their kind
        self._zip_inbox = ImportZipInbox(
            inbox_dir=self._paths.inbox,
            unpacking_dir=self._paths.unpacking,
            finished_dir=self._paths.finished,
            refused_dir=self._paths.refused,
        )
        if wants_ticks:
            self._tick_importer = TickDataImporter(
                source_dir=str(self._paths.inbox),
                target_dir=str(self._paths.tick_target),
                override=self._override,
                move_processed_files=self._paths.finished is not None,
                finished_dir=finished_dir,
                auto_render_bars=False,
                refused_dir=refused_dir,
            )
            # Its own step, timed by the zips' size: unpacking a large zip takes long, and
            # inside the import step it would distort that step's measured rate
            tracker.add_step(ImportStepId.TICKS_UNPACK, ImportSection.TICKS,
                             'Unpack zips', counter_unit='files')
            tracker.add_step(ImportStepId.TICKS_IMPORT, ImportSection.TICKS,
                             'Import tick files', counter_unit='files')
            tracker.add_step(ImportStepId.TICKS_INDEX, ImportSection.TICKS,
                             'Tick index', counter_unit='files')
            tracker.add_step(ImportStepId.TICKS_ORDER, ImportSection.TICKS,
                             'Check archive order')
            if renders_bars:
                tracker.add_step(ImportStepId.BARS_RENDER, ImportSection.TICKS,
                                 'Render bars', counter_unit='symbols')
                tracker.add_step(ImportStepId.BARS_INDEX, ImportSection.TICKS,
                                 'Rebuild bar index', counter_unit='files')
        if wants_signals:
            self._signal_importer = SignalDataImporter(
                source_dir=str(self._paths.inbox),
                target_dir=str(self._paths.signal_target),
                override=self._override,
                move_processed_files=self._paths.finished is not None,
                finished_dir=finished_dir,
                refused_dir=refused_dir,
            )
            tracker.add_step(ImportStepId.SIGNALS_UNPACK, ImportSection.SIGNALS,
                             'Unpack zips', counter_unit='files')
            tracker.add_step(ImportStepId.SIGNALS_IMPORT, ImportSection.SIGNALS,
                             'Import signal files', counter_unit='files')
            tracker.add_step(ImportStepId.SIGNALS_INDEX, ImportSection.SIGNALS,
                             'Rebuild signal index', counter_unit='files')
        if renders_bars:
            tracker.add_step(ImportStepId.CACHES, ImportSection.SHARED,
                             'Rebuild discovery caches', counter_unit='caches')
            # The caches resolve their own paths and describe the production archive only;
            # an import scoped elsewhere says so instead of leaving the step out
            if not self._writes_production:
                tracker.skip_step(ImportStepId.CACHES, 'not the production archive')
        tracker.add_step(ImportStepId.PACKAGES_RECEIPT, ImportSection.SHARED,
                         'Acknowledge packages', counter_unit='packages')
        if fetch_skipped_because:
            tracker.skip_step(ImportStepId.PACKAGES_RECEIPT, fetch_skipped_because)
        elif not self._receipts_due():
            tracker.skip_step(ImportStepId.PACKAGES_RECEIPT, 'no package to acknowledge')
        tracker.publish(force=True)

        scan = self._scan_inboxes()
        tick_bytes = sum(entry.size_bytes for entry in scan.tick_files)
        signal_bytes = sum(entry.size_bytes for entry in scan.signal_files)
        tracker.set_inbox(len(scan.tick_files), tick_bytes,
                          len(scan.signal_files), signal_bytes)
        total_files = len(scan.tick_files) + len(scan.signal_files)
        tracker.advance(ImportStepId.SCAN, total_files, total_files)
        self._show_scan_result(scan)
        tracker.finish_step(ImportStepId.SCAN)
        self._scan = scan
        self._by_hand = self._inbox_by_hand(scan)

        if wants_ticks:
            self._plan_tick_quantities(scan, tick_bytes)
        if wants_signals:
            self._plan_signal_quantities(scan, signal_bytes)
        tracker.publish(force=True)

    def _inbox_by_hand(self, scan: InboxScan) -> InboxByHand:
        """
        What the scan found that no package service delivered in this import — read before any
        file leaves the inbox, while a carried-in manifest still lies beside its zip.

        Args:
            scan: The inbox as the scan found it

        Returns:
            The loose files and the zips that came by hand
        """
        delivered = {fetch.manifest.zip_name for fetch in self._package_fetches
                     if fetch.manifest is not None and fetch.outcome in (
                         PackageFetchOutcome.FETCHED, PackageFetchOutcome.IN_INBOX)}
        zips = [content.zip_path for content in scan.tick_zips + scan.signal_zips
                if content.zip_path.name not in delivered]
        return InboxByHand(
            loose_tick_files=sum(1 for entry in scan.tick_files if entry.zip_member is None),
            loose_signal_files=sum(1 for entry in scan.signal_files if entry.zip_member is None),
            zips_without_manifest=[path.name for path in zips
                                   if not manifest_companion(path).exists()],
            zips_with_manifest=[path.name for path in zips if manifest_companion(path).exists()])

    def _open_package_clients(self, wants_ticks: bool, wants_signals: bool) -> Optional[str]:
        """
        Open a client for every switched-on package service whose packages feed an archive this
        import writes.

        Args:
            wants_ticks: Whether the import writes the tick archive
            wants_signals: Whether the import writes the signal archive

        Returns:
            Why nothing is fetched, None when at least one service is reached
        """
        if self._package_services is None:
            return 'not asked for — an import fetches with --fetch'
        wanted = {section for section, wants in ((ImportSection.TICKS, wants_ticks),
                                                 (ImportSection.SIGNALS, wants_signals)) if wants}
        services = [service for service in self._package_services if service.delivers in wanted]
        for service in services:
            if service.state is PackageServiceState.ON:
                self._package_clients[service.package_service] = self._client_factory(
                    service.source)
        if self._package_clients:
            return None
        if not services:
            return "no package service feeds this import's archives"
        return ' · '.join(service.describe_state() for service in services)

    def _run_fetch_step(self) -> None:
        """
        Fetch the packages that wait for our receipt from every opened package service into the
        import inbox, each proven by its hash before it counts as arrived. Every service's list
        is read first, then the packages are fetched in turn. A service failing stops its own
        fetch, never another service's and never the import: what lies in the inbox is imported
        all the same.

        Returns:
            None
        """
        tracker = self._tracker
        tracker.start_step(ImportStepId.PACKAGES_FETCH)
        ready: List[Tuple[str, PackageListing]] = []
        failed_services: List[str] = []
        for package_service, client in self._package_clients.items():
            listings: Optional[List[PackageListing]]
            try:
                listings = client.list_packages()
            except (KeyError, TypeError, ValueError) as error:
                failed_services.append(package_service)
                self._count_package_error(
                    f'PACKAGES {package_service}: the package list could not be read: {error}')
                continue
            if listings is None:
                failed_services.append(package_service)
                self._count_package_error(
                    f'PACKAGES {package_service}: the package service did not answer — nothing '
                    'fetched from it; the import goes on with what lies in the inbox')
                continue
            ready.extend((package_service, listing) for listing in listings
                         if listing.state is PackageState.READY)

        tracker.set_quantity(ImportStepId.PACKAGES_FETCH, ImportRateUnit.MB,
                             sum(listing.size_bytes for _, listing in ready) / BYTES_PER_MB)
        tracker.set_total(ImportStepId.PACKAGES_FETCH, len(ready))
        for done, (package_service, listing) in enumerate(ready):
            tracker.advance(ImportStepId.PACKAGES_FETCH, done,
                            current_item=f'{package_service} {listing.package_id} '
                                         f'({format_size(listing.size_bytes)})')
            self._package_fetches.append(self._fetch_package(package_service, listing))
            self._update_counters()
        tracker.advance(ImportStepId.PACKAGES_FETCH, len(ready))
        not_reached = [service.describe_state() for service in self._package_services
                       if service.package_service not in self._package_clients]
        tracker.set_detail(ImportStepId.PACKAGES_FETCH, ' · '.join(
            [_fetch_counts_by_service(self._package_fetches) or 'nothing waits']
            + [f'{name}: did not answer' for name in failed_services] + not_reached))
        fetched_all = not failed_services and not any(
            fetch.outcome is PackageFetchOutcome.FAILED for fetch in self._package_fetches)
        tracker.finish_step(ImportStepId.PACKAGES_FETCH, _status_after(fetched_all))

    def _fetch_package(self, package_service: str, listing: PackageListing) -> PackageFetch:
        """
        Fetch one package into the inbox, unless the finished or the refused directory holds its
        zip already, or the inbox does under its own name and hash. A download that broke off is
        resumed by the next attempt or the next import.

        Args:
            package_service: The service that listed it
            listing: The package as listed

        Returns:
            What the fetch did with it
        """
        client = self._package_clients[package_service]
        try:
            read = client.read_manifest(listing.package_id)
        except (KeyError, TypeError, ValueError) as error:
            return self._fetch_failed(package_service, listing,
                                      f'its manifest could not be read: {error}')
        if read is None:
            return self._fetch_failed(package_service, listing,
                                      'the package service did not answer')
        manifest, served = read
        zip_name = manifest.zip_name
        if Path(zip_name).name != zip_name or not zip_name.endswith('.zip'):
            return self._fetch_failed(package_service, listing,
                                      f"its zip is named '{zip_name}', not a plain zip file name")
        if manifest.zip_sha256 != listing.sha256:
            return self._fetch_failed(package_service, listing,
                                      'its manifest and the list name different zips')

        finished = self._paths.finished
        refused = self._paths.refused
        if (finished is not None and (finished / zip_name).exists()
                and file_sha256(finished / zip_name) == listing.sha256):
            self._package_manifests[zip_name] = manifest
            return PackageFetch(package_service=package_service, listing=listing,
                                manifest=manifest, outcome=PackageFetchOutcome.ALREADY_HERE)
        if refused is not None and (refused / zip_name).exists():
            self._count_package_warning(
                f'PACKAGE {listing.package_id} NOT FETCHED: its zip lies in {refused} from an '
                'earlier import — it is not acknowledged until somebody has looked at it')
            return PackageFetch(package_service=package_service, listing=listing,
                                manifest=manifest, outcome=PackageFetchOutcome.REFUSED_BEFORE,
                                detail=f'its zip lies in {refused}')

        inbox = create_directory(self._paths.inbox,
                                 holds='the files waiting for their import')
        target = inbox / zip_name
        # Recognised by its own name first, then by its hash: a copy under another name is not
        # this package, and is imported as a zip of its own
        in_inbox = target.exists() and file_sha256(target) == listing.sha256
        if not in_inbox:
            part = inbox / f'{zip_name}.part'
            if part.exists() and part.stat().st_size >= listing.size_bytes:
                if file_sha256(part) != listing.sha256:
                    part.unlink()
            if not part.exists() or part.stat().st_size < listing.size_bytes:
                if not client.download_zip(listing.package_id, part):
                    return self._fetch_failed(
                        package_service, listing,
                        'the download did not finish — what arrived is kept and the next import '
                        'resumes it')
            if file_sha256(part) != listing.sha256:
                part.unlink()
                return self._fetch_failed(package_service, listing,
                                          'what arrived does not match the zip\'s hash — removed')
            part.replace(target)
        manifest_companion(target).write_text(json.dumps(served, indent=2), encoding='utf-8')
        self._package_manifests[zip_name] = manifest
        if in_inbox:
            vLog.info(f'📦 Found {package_service} {listing.package_id} in the inbox: {zip_name} '
                      f'({format_size(listing.size_bytes)}), its hash proven — not downloaded')
        else:
            vLog.info(f'📦 Fetched {package_service} {listing.package_id}: {zip_name} '
                      f'({format_size(listing.size_bytes)}), its hash proven')
        if manifest.held_for_next:
            vLog.info(f'   {len(manifest.held_for_next)} file(s) reach past its cutoff and come '
                      'with the next package')
        for excluded in manifest.excluded:
            self._count_package_warning(f'PACKAGE {listing.package_id} LEFT OUT {excluded}')
        return PackageFetch(package_service=package_service, listing=listing, manifest=manifest,
                            outcome=(PackageFetchOutcome.IN_INBOX if in_inbox
                                     else PackageFetchOutcome.FETCHED))

    def _fetch_failed(self, package_service: str, listing: PackageListing,
                      detail: str) -> PackageFetch:
        """
        Count a package the fetch could not bring in.

        Args:
            package_service: The service that listed it
            listing: The package
            detail: Why

        Returns:
            Its failed fetch
        """
        self._count_package_error(
            f'PACKAGE {package_service} {listing.package_id} NOT FETCHED: {detail}')
        return PackageFetch(package_service=package_service, listing=listing,
                            outcome=PackageFetchOutcome.FAILED, detail=detail)

    def _receipts_due(self) -> bool:
        """
        Whether any package may need its receipt once the import is through.

        Returns:
            True when a package was fetched, or its zip was found in the inbox or the finished
            directory
        """
        return any(fetch.outcome in _ACKNOWLEDGEABLE for fetch in self._package_fetches)

    def _manifest_for(self, zip_path: Path) -> Optional[PackageManifest]:
        """
        The package manifest a zip came with: the one the fetch read, or the one beside it in the
        inbox when the package was carried in by hand.

        Args:
            zip_path: The zip

        Returns:
            Its manifest, None for a zip that came without one
        """
        manifest = self._package_manifests.get(zip_path.name)
        if manifest is not None:
            return manifest
        companion = manifest_companion(zip_path)
        if not companion.exists():
            return None
        manifest = parse_manifest(json.loads(companion.read_text(encoding='utf-8')))
        self._package_manifests[zip_path.name] = manifest
        return manifest

    def _manifest_disagreement(self, content: InboxZipContent) -> Optional[str]:
        """
        Whether a zip's files differ from the ones its package manifest names — in their names
        or in their sizes. The hashes are held to it while each member is unpacked.

        Args:
            content: The zip's content

        Returns:
            How they differ, None when they agree or the zip came without a manifest
        """
        try:
            manifest = self._manifest_for(content.zip_path)
        except (KeyError, TypeError, ValueError) as error:
            return f'the manifest beside it cannot be read: {error}'
        if manifest is None:
            return None
        names = {member.name for member in content.members}
        missing = sorted(set(manifest.member_sha256) - names)
        extra = sorted(names - set(manifest.member_sha256))
        if missing or extra:
            return (f'its files differ from its package manifest — missing: '
                    f"{', '.join(missing) or 'none'}; not named: {', '.join(extra) or 'none'}")
        # The size each member unpacks to, from the zip's central directory, against the size
        # the manifest names — checked before anything is unpacked
        resized = sorted(f'{member.name} ({member.size_bytes} bytes, the manifest names '
                         f'{manifest.member_size_bytes[member.name]})'
                         for member in content.members
                         if member.name in manifest.member_size_bytes
                         and manifest.member_size_bytes[member.name] != member.size_bytes)
        if resized:
            return f"its files differ in size from its package manifest — {', '.join(resized)}"
        return None

    def _unpack_checked(self, member: InboxZipMember) -> Path:
        """
        Unpack one member, and hold it to its package manifest's hash where the zip has one.

        Args:
            member: The member

        Returns:
            The unpacked file
        """
        path, digest = self._zip_inbox.unpack_member(member)
        manifest = self._manifest_for(member.zip_path)
        if manifest is not None and manifest.member_sha256.get(member.name) != digest:
            raise InboxZipUnpackException(
                f"'{member.name}' does not match the hash its package manifest names")
        return path

    def _scan_inboxes(self) -> InboxScan:
        """
        List the inbox by kind: every file's size, and for a tick file its broker from the
        metadata block at its head. Only for the screen and the estimate — the import
        itself parses every file in full.

        Returns:
            InboxScan
        """
        scan = InboxScan()
        if self._tick_importer is not None:
            for path in self._tick_importer.collect_files():
                scan.tick_files.append(InboxFile(
                    path=path, size_bytes=path.stat().st_size,
                    broker_type=_read_tick_broker_type(path)))
        if self._signal_importer is not None:
            for path in self._signal_importer.collect_files():
                scan.signal_files.append(InboxFile(path=path, size_bytes=path.stat().st_size))
        self._scan_zips(scan)
        return scan

    def _scan_zips(self, scan: InboxScan) -> None:
        """
        List the zips of the inbox from their central directories. A zip refused as a whole is
        settled here, before anything is written; the members of every other zip join the files
        of their kind — a tick export with its size unpacked and its broker from its head, a
        signal file with its size. A zip whose kind this import leaves out (--only) stays in the
        inbox untouched.

        Args:
            scan: The scan, its loose files already listed

        Returns:
            None
        """
        taken_names = {entry.path.name for entry in scan.tick_files}
        for zip_path in self._zip_inbox.collect_zips():
            content = self._zip_inbox.read_content(zip_path, taken_names)
            if content.refusal is None:
                content.refusal = self._manifest_disagreement(content)
            if content.refusal is not None:
                self._settle_refused_zip(content)
                continue
            if content.kind is InboxFileKind.TICK_EXPORT and self._tick_importer is not None:
                scan.tick_zips.append(content)
                for member in content.members:
                    taken_names.add(member.name)
                    scan.tick_files.append(InboxFile(
                        path=zip_path, size_bytes=member.size_bytes,
                        broker_type=_broker_type_in_head(
                            self._zip_inbox.read_member_head(member, _HEAD_BYTES)),
                        zip_member=member))
            elif content.kind is InboxFileKind.SIGNAL_FILE and self._signal_importer is not None:
                scan.signal_zips.append(content)
                for member in content.members:
                    scan.signal_files.append(InboxFile(
                        path=zip_path, size_bytes=member.size_bytes, zip_member=member))

    def _settle_refused_zip(self, content: InboxZipContent) -> None:
        """
        Move a zip refused as a whole to the refused directory, before anything of it is
        written, and report it.

        Args:
            content: The zip's content, with its refusal

        Returns:
            None
        """
        settlement = self._zip_inbox.refuse(content)
        self._zip_settlements.append(settlement)
        self._report_settlement(settlement)

    def _show_scan_result(self, scan: InboxScan) -> None:
        """
        Write what the scan found beside the scan step, and skip what has nothing to do.

        Args:
            scan: The scan result

        Returns:
            None
        """
        per_broker: Dict[str, int] = {}
        for entry in scan.tick_files:
            per_broker[entry.broker_type or '?'] = per_broker.get(entry.broker_type or '?', 0) + 1
        parts = [f'{broker} {count}' for broker, count in sorted(per_broker.items())]
        if scan.signal_files:
            parts.append(f'signals {len(scan.signal_files)}')
        zips = scan.tick_zips + scan.signal_zips
        if zips:
            members = sum(len(content.members) for content in zips)
            parts.append(f'{len(zips)} zip(s), {members} of the files inside')
        self._tracker.set_detail(ImportStepId.SCAN, ' · '.join(parts) or 'the inbox is empty')

        if self._tick_importer is not None and not scan.tick_files:
            self._skip_section(ImportSection.TICKS, 'no tick file in the inbox')
        elif self._tick_importer is not None and not scan.tick_zips:
            self._tracker.skip_step(ImportStepId.TICKS_UNPACK, 'no tick zip in the inbox')
        if self._signal_importer is not None and not scan.signal_files:
            self._skip_section(ImportSection.SIGNALS, 'no signal file in the inbox')
        elif self._signal_importer is not None and not scan.signal_zips:
            self._tracker.skip_step(ImportStepId.SIGNALS_UNPACK, 'no signal zip in the inbox')
        if not scan.tick_files and self._tracker.is_pending(ImportStepId.CACHES):
            self._tracker.skip_step(ImportStepId.CACHES, 'no bars rendered')

    def _skip_section(self, section: ImportSection, reason: str) -> None:
        """
        Mark every step of a section skipped.

        Args:
            section: The section
            reason: Shown beside each step

        Returns:
            None
        """
        for step_id in self._tracker.step_ids(section):
            self._tracker.skip_step(step_id, reason)

    def _plan_tick_quantities(self, scan: InboxScan, tick_bytes: int) -> None:
        """
        Declare what each tick step scales with, from the scan and the indexes already there.

        Args:
            scan: The scan result
            tick_bytes: Size of the tick files found

        Returns:
            None
        """
        tracker = self._tracker
        target = self._paths.tick_target
        if scan.tick_zips:
            # By the zips' own size, as they lie in the inbox: what the unpacking reads
            zip_bytes = sum(content.zip_path.stat().st_size for content in scan.tick_zips)
            tracker.set_quantity(ImportStepId.TICKS_UNPACK, ImportRateUnit.MB,
                                 zip_bytes / BYTES_PER_MB)
            tracker.set_total(ImportStepId.TICKS_UNPACK,
                              sum(len(content.members) for content in scan.tick_zips))
        tracker.set_quantity(ImportStepId.TICKS_IMPORT, ImportRateUnit.MB,
                             tick_bytes / BYTES_PER_MB)
        tracker.set_total(ImportStepId.TICKS_IMPORT, len(scan.tick_files))

        tick_files = _index_row_count(target / TickIndexManager.INDEX_FILE_PARQUET)
        rebuild_reason = TickIndexManager(data_dir=str(target)).get_full_rebuild_reason()
        if rebuild_reason is None:
            # An update reads in full only the files this import writes; checking the others is
            # one stat each, run in parallel, and is left out of the quantity
            tracker.set_quantity(ImportStepId.TICKS_INDEX, ImportRateUnit.IMPORTED_FILE,
                                 len(scan.tick_files))
            tracker.set_detail(ImportStepId.TICKS_INDEX, 'update — only the files that changed')
        else:
            tracker.set_quantity(ImportStepId.TICKS_INDEX, ImportRateUnit.FILE,
                                 tick_files + len(scan.tick_files))
            tracker.set_detail(ImportStepId.TICKS_INDEX,
                               f'full rebuild — {_INDEX_REBUILD_REASON_TEXTS[rebuild_reason]}')
        tracker.set_quantity(ImportStepId.TICKS_ORDER, ImportRateUnit.FILE,
                             tick_files + len(scan.tick_files))

        if tracker.has_step(ImportStepId.BARS_RENDER):
            brokers = {entry.broker_type for entry in scan.tick_files if entry.broker_type}
            ticks, symbols = _ticks_and_symbols(
                target / TickIndexManager.INDEX_FILE_PARQUET, brokers)
            tracker.set_quantity(ImportStepId.BARS_RENDER, ImportRateUnit.MILLION_TICKS,
                                 ticks / 1e6, workers=self._render_workers(symbols))
            tracker.set_total(ImportStepId.BARS_RENDER, symbols or None)
            bar_files = _index_row_count(target / BarsIndexManager.INDEX_FILE_PARQUET)
            tracker.set_quantity(ImportStepId.BARS_INDEX, ImportRateUnit.BAR_FILE, bar_files)
            if tracker.is_pending(ImportStepId.CACHES):
                tracker.set_quantity(ImportStepId.CACHES, ImportRateUnit.BAR_FILE, bar_files)

    def _plan_signal_quantities(self, scan: InboxScan, signal_bytes: int) -> None:
        """
        Declare what each signal step scales with.

        Args:
            scan: The scan result
            signal_bytes: Size of the signal files found

        Returns:
            None
        """
        tracker = self._tracker
        if scan.signal_zips:
            # By the zips' own size, as they lie in the inbox: what the unpacking reads
            zip_bytes = sum(content.zip_path.stat().st_size for content in scan.signal_zips)
            tracker.set_quantity(ImportStepId.SIGNALS_UNPACK, ImportRateUnit.MB,
                                 zip_bytes / BYTES_PER_MB)
            tracker.set_total(ImportStepId.SIGNALS_UNPACK,
                              sum(len(content.members) for content in scan.signal_zips))
        tracker.set_quantity(ImportStepId.SIGNALS_IMPORT, ImportRateUnit.MB,
                             signal_bytes / BYTES_PER_MB)
        tracker.set_total(ImportStepId.SIGNALS_IMPORT, len(scan.signal_files))
        signal_files = _distinct_paths(
            self._paths.signal_target / SignalIndexManager.INDEX_FILE_PARQUET)
        tracker.set_quantity(ImportStepId.SIGNALS_INDEX, ImportRateUnit.FILE,
                             signal_files + len(scan.signal_files))

    def _render_workers(self, symbols: int) -> int:
        """
        The worker count a render of this many symbols runs with.

        Args:
            symbols: Symbols to render

        Returns:
            Effective worker count
        """
        return effective_render_workers(self._import_config.get_bar_render_workers(), symbols)

    # =========================================================================
    # STEPS
    # =========================================================================

    def _run_steps(self) -> None:
        """
        Run every planned step in order.

        Returns:
            None
        """
        if self._tick_importer is not None and self._scan.tick_files:
            self._run_tick_steps()
        if self._signal_importer is not None and self._scan.signal_files:
            self._run_signal_steps()
        if (self._tracker.is_pending(ImportStepId.CACHES)
                and self._bar_importer is not None):
            self._run_cache_step()
        if self._tracker.is_pending(ImportStepId.PACKAGES_RECEIPT):
            self._run_receipt_step()

    def _run_tick_steps(self) -> None:
        """
        Import the tick files, rebuild and check the tick index, render the bars.

        Returns:
            None
        """
        tracker = self._tracker
        importer = self._tick_importer
        unpacked = self._run_unpack_step(self._scan.tick_zips, ImportStepId.TICKS_UNPACK)
        # A zip refused while unpacking takes its members out of the import
        files = [entry for entry in self._scan.tick_files
                 if entry.zip_member is None or entry.zip_member in unpacked]
        zip_results: Dict[Path, Dict[str, InboxImportResult]] = {}

        tracker.start_step(ImportStepId.TICKS_IMPORT, total=len(files))
        importer.log_header(len(files))
        for done, entry in enumerate(files):
            name = entry.path.name if entry.zip_member is None else entry.zip_member.name
            tracker.advance(ImportStepId.TICKS_IMPORT, done,
                            current_item=f'{entry.broker_type or "?"}/{name} '
                                         f'({format_size(entry.size_bytes)})')
            if entry.zip_member is None:
                importer.import_file(entry.path)
            else:
                member = entry.zip_member
                zip_results.setdefault(member.zip_path, {})[member.name] = \
                    self._import_zip_member(importer, member, unpacked[member])
            self._update_counters()
        tracker.advance(ImportStepId.TICKS_IMPORT, len(files))
        tracker.finish_step(ImportStepId.TICKS_IMPORT)
        self._settle_zips(self._scan.tick_zips, zip_results)

        tracker.start_step(ImportStepId.TICKS_INDEX)
        errors_before = len(importer.errors)
        index_manager = importer.update_parquet_index(
            on_file_scanned=lambda done, total: tracker.advance(
                ImportStepId.TICKS_INDEX, done, total))
        self._update_counters()
        if index_manager is not None:
            self._tick_index_run = index_manager.get_last_run()
        if self._tick_index_run is not None:
            tracker.set_detail(ImportStepId.TICKS_INDEX, _index_run_phrase(self._tick_index_run))
        # A file the scan could not read fails the step, while the index it built — without
        # that file — still serves the order check and the render
        tracker.finish_step(ImportStepId.TICKS_INDEX, _status_after(
            index_manager is not None and len(importer.errors) == errors_before))

        if index_manager is None:
            tracker.skip_step(ImportStepId.TICKS_ORDER, 'no tick index')
        else:
            tracker.start_step(ImportStepId.TICKS_ORDER)
            importer.validate_archive_ordering(index_manager)
            self._update_counters()
            tracker.finish_step(ImportStepId.TICKS_ORDER)

        if not tracker.has_step(ImportStepId.BARS_RENDER):
            return
        if importer.processed_files == 0:
            tracker.skip_step(ImportStepId.BARS_RENDER, 'no file imported')
            tracker.skip_step(ImportStepId.BARS_INDEX, 'no file imported')
            if tracker.is_pending(ImportStepId.CACHES):
                tracker.skip_step(ImportStepId.CACHES, 'no bars rendered')
            return
        self._run_render_steps(index_manager)

    def _run_unpack_step(self, zips: List[InboxZipContent],
                         step_id: ImportStepId) -> Dict[InboxZipMember, Path]:
        """
        Unpack the members of each zip of one kind into its own folder, ahead of their import —
        a step of its own, timed by the zips' size. A zip one of whose members cannot be
        unpacked is refused as a whole; one that does not fit on the disk, or whose unpacking
        the file system refuses, stays in the inbox for the next import.

        Args:
            zips: The zips of one kind
            step_id: That kind's unpack step

        Returns:
            Each unpacked member's file, by member
        """
        unpacked: Dict[InboxZipMember, Path] = {}
        if not zips:
            return unpacked
        tracker = self._tracker
        total = sum(len(content.members) for content in zips)
        tracker.start_step(step_id, total=total)
        done = 0
        all_unpacked = True
        for content in zips:
            self._zip_inbox.clear_unpacking_folder(content.zip_path)
            settlement: Optional[InboxZipSettlement] = None
            shortage = self._zip_inbox.get_disk_shortage(content)
            zip_unpacked: Dict[InboxZipMember, Path] = {}
            if shortage is not None:
                settlement = self._zip_inbox.keep(content.zip_path, shortage)
            else:
                try:
                    for member in content.members:
                        tracker.advance(
                            step_id, done + len(zip_unpacked),
                            current_item=f'{content.zip_path.name}/{member.name} '
                                         f'({format_size(member.size_bytes)})')
                        zip_unpacked[member] = self._unpack_checked(member)
                except InboxZipUnpackException as error:
                    settlement = self._zip_inbox.refuse(
                        InboxZipContent(zip_path=content.zip_path, refusal=str(error)))
                except OSError as error:
                    settlement = self._zip_inbox.keep(
                        content.zip_path, f'the file system refused the unpacking: {error}')
            done += len(content.members)
            if settlement is not None:
                all_unpacked = False
                self._zip_settlements.append(settlement)
                self._report_settlement(settlement)
            else:
                unpacked.update(zip_unpacked)
            self._update_counters()
        tracker.advance(step_id, total)
        # A zip left out ends the step FAILED: its time covered less than the whole work
        tracker.finish_step(step_id, _status_after(all_unpacked))
        return unpacked

    def _import_zip_member(self, importer: Union[TickDataImporter, SignalDataImporter],
                           member: InboxZipMember, unpacked: Path) -> InboxImportResult:
        """
        Import one unpacked member. Its copy goes at once when the archive took it; the copy of
        a member refused or failed on waits for the zip's settlement.

        Args:
            importer: The import of the member's kind
            member: The member
            unpacked: Its unpacked file

        Returns:
            How its import ended
        """
        result = importer.import_file_result(unpacked, archived_in=member.zip_path)
        if result.outcome.is_taken():
            unpacked.unlink(missing_ok=True)
        return result

    def _settle_zips(self, zips: List[InboxZipContent],
                     zip_results: Dict[Path, Dict[str, InboxImportResult]]) -> None:
        """
        Move each zip of one kind that went through the import out of the inbox, and report it.

        Args:
            zips: The zips of one kind
            zip_results: Each zip's member results, by zip — only zips that reached the import

        Returns:
            None
        """
        for content in zips:
            results = zip_results.get(content.zip_path)
            if results is None:
                continue
            settlement = self._zip_inbox.settle(content, results)
            self._zip_settlements.append(settlement)
            self._report_settlement(settlement)
        self._update_counters()

    def _report_settlement(self, settlement: InboxZipSettlement) -> None:
        """
        Log where a zip went. Each member's own outcome is counted by the importer already — a
        refused or failed file as an error, a duplicate as a warning — so the zip adds an error
        of its own only where nothing else names the cause: a zip refused as a whole, one that
        stayed in the inbox, a file that could not be moved.

        Args:
            settlement: The zip's settlement

        Returns:
            None
        """
        name = settlement.zip_path.name
        move = settlement.move
        if move.disposition is InboxDisposition.MOVED_TO_FINISHED:
            vLog.info(f'📦 {name}: {_member_counts(settlement)} → moved to {move.destination}')
        elif move.disposition is InboxDisposition.ALREADY_THERE:
            vLog.info(f'📦 {name}: {_member_counts(settlement)} → an identical {name} lies in '
                      f'{move.destination}; the inbox copy was removed')
        elif move.disposition is InboxDisposition.MOVED_TO_REFUSED:
            message = f'ZIP REFUSED {name} → moved to {move.destination}: {settlement.reason}'
            if settlement.results:
                # Nothing in it got into the archive; each file's own error is counted
                vLog.warning(message)
            else:
                self._count_zip_error(message)
        elif move.destination is None and settlement.results:
            vLog.info(f'📦 {name}: {move.detail}')
        else:
            self._count_zip_error(f'ZIP KEPT IN THE INBOX {name}: {settlement.reason}')

        for member, member_move in settlement.member_moves.items():
            if member_move.disposition is InboxDisposition.KEPT_IN_INBOX:
                folder = self._zip_inbox.get_unpacking_folder(settlement.zip_path)
                self._count_zip_error(f'KEPT IN {folder} {member}: {member_move.detail}')
            else:
                vLog.info(f'   → {member} moved to {member_move.destination}, the reason '
                          'beside it')

    def _refused_names(self) -> List[str]:
        """
        Everything this import moved to the refused directory: loose files, zips, and the
        files of a zip refused on their own.

        Returns:
            Their names, loose files first
        """
        names = self._tick_importer.get_refused_files() if self._tick_importer is not None else []
        if self._signal_importer is not None:
            names.extend(self._signal_importer.get_refused_files())
        for settlement in self._zip_settlements:
            if settlement.move.disposition is InboxDisposition.MOVED_TO_REFUSED:
                names.append(settlement.zip_path.name)
            names.extend(member for member, move in settlement.member_moves.items()
                         if move.disposition is InboxDisposition.MOVED_TO_REFUSED)
        return names

    def _count_zip_error(self, message: str) -> None:
        """
        Log a zip's error and count it with the import's errors.

        Args:
            message: The error

        Returns:
            None
        """
        vLog.error(message)
        self._zip_errors.append(message)

    def _run_receipt_step(self) -> None:
        """
        Acknowledge each fetched package whose zip now lies in the finished directory, with the
        hash of the zip we hold. A zip elsewhere is not acknowledged: it stays waiting at the
        collector, which reports it overdue after a while.

        Returns:
            None
        """
        tracker = self._tracker
        due = [fetch for fetch in self._package_fetches
               if fetch.manifest is not None and fetch.outcome in _ACKNOWLEDGEABLE]
        tracker.start_step(ImportStepId.PACKAGES_RECEIPT, total=len(due))
        failed = False
        for done, fetch in enumerate(due):
            package_id = fetch.listing.package_id
            tracker.advance(ImportStepId.PACKAGES_RECEIPT, done, current_item=package_id)
            held = (self._paths.finished / fetch.manifest.zip_name
                    if self._paths.finished is not None else None)
            if held is None or not held.exists():
                self._count_package_warning(
                    f'PACKAGE {package_id} NOT ACKNOWLEDGED: its zip did not reach the finished '
                    'directory — see the zip\'s line in the summary')
                continue
            digest = file_sha256(held)
            if digest != fetch.listing.sha256:
                failed = True
                self._count_package_error(
                    f'PACKAGE {package_id} NOT ACKNOWLEDGED: {held} is not that package\'s zip')
                continue
            # The receipt goes to the service the package came from
            outcome, words = self._package_clients[fetch.package_service].send_receipt(
                package_id, digest, self._receipt_note(fetch))
            self._receipts[(fetch.package_service, package_id)] = (outcome, words)
            if outcome in (PackageReceiptOutcome.ACKNOWLEDGED,
                           PackageReceiptOutcome.ALREADY_ACKNOWLEDGED):
                vLog.info(f'📦 Acknowledged {package_id}')
            elif outcome is PackageReceiptOutcome.UNRESOLVED:
                self._count_package_warning(
                    f'PACKAGE {package_id} RECEIPT UNRESOLVED: no answer, and reading it back '
                    'showed no receipt — the next import sends it again')
            else:
                failed = True
                self._count_package_error(
                    f'PACKAGE {package_id} RECEIPT {outcome.value.upper()}: {words}')
            self._update_counters()
        tracker.advance(ImportStepId.PACKAGES_RECEIPT, len(due))
        tracker.finish_step(ImportStepId.PACKAGES_RECEIPT, _status_after(not failed))

    def _receipt_note(self, fetch: PackageFetch) -> str:
        """
        What the import did with a package, for its receipt.

        Args:
            fetch: The package's fetch

        Returns:
            The note
        """
        zip_name = fetch.manifest.zip_name
        settlement = next((settlement for settlement in self._zip_settlements
                           if settlement.zip_path.name == zip_name), None)
        if settlement is None:
            return 'imported by an earlier import'
        note = _member_counts(settlement)
        refused = [name for name, result in settlement.results.items()
                   if not result.outcome.is_taken()]
        if refused:
            note += ' — refused: ' + ', '.join(refused)
        return note

    def _count_package_warning(self, message: str) -> None:
        """
        Log a warning of the package exchange and count it.

        Args:
            message: The warning

        Returns:
            None
        """
        vLog.warning(message)
        self._package_warnings.append(message)

    def _count_package_error(self, message: str) -> None:
        """
        Log an error of the package exchange and count it.

        Args:
            message: The error

        Returns:
            None
        """
        vLog.error(message)
        self._package_errors.append(message)

    def _run_render_steps(self, index_manager: Optional[TickIndexManager]) -> None:
        """
        Render the bars of the imported broker types, then rebuild the bar index — the index
        also when the render was interrupted, so it describes the files that are there. A step
        that counted errors ends FAILED: its time measured less than the whole work, so it
        must not become the stored rate either.

        Args:
            index_manager: The rebuilt tick index, for the exact tick count

        Returns:
            None
        """
        tracker = self._tracker
        brokers = self._tick_importer.get_processed_broker_types()
        if index_manager is not None:
            ticks, symbols = _ticks_and_symbols_from_index(index_manager, brokers)
            tracker.set_quantity(ImportStepId.BARS_RENDER, ImportRateUnit.MILLION_TICKS,
                                 ticks / 1e6, workers=self._render_workers(symbols))
        self._bar_importer = BarImporter(data_dir=str(self._paths.tick_target))
        symbol_count = sum(len(self._bar_importer.tick_index.list_symbols(bt)) for bt in brokers)

        tracker.start_step(ImportStepId.BARS_RENDER, total=symbol_count)
        errors_before = len(self._bar_importer.errors)
        try:
            self._bar_importer.render_bars_for_all_symbols(
                broker_types=brokers,
                on_symbol_done=tracker.on_symbol_done,
                on_worker_progress=tracker.on_worker_progress,
            )
            self._update_counters()
            tracker.finish_step(ImportStepId.BARS_RENDER, _status_after(
                len(self._bar_importer.errors) == errors_before))
        except KeyboardInterrupt:
            tracker.finish_step(ImportStepId.BARS_RENDER, ImportStepStatus.INTERRUPTED)
            raise
        finally:
            tracker.start_step(ImportStepId.BARS_INDEX)
            indexed = self._bar_importer.update_bar_index(
                on_file_scanned=lambda done, total: tracker.advance(
                    ImportStepId.BARS_INDEX, done, total))
            self._update_counters()
            tracker.finish_step(ImportStepId.BARS_INDEX, _status_after(indexed))
            # The caches read the bar index: over a failed one they would describe stale bars
            if not indexed and tracker.is_pending(ImportStepId.CACHES):
                tracker.skip_step(ImportStepId.CACHES, 'bar index not rebuilt')

    def _run_signal_steps(self) -> None:
        """
        Unpack the signal zips, import the signal files — loose ones and the members of each
        zip — settle the zips, and rebuild the signal index.

        Returns:
            None
        """
        tracker = self._tracker
        importer = self._signal_importer
        unpacked = self._run_unpack_step(self._scan.signal_zips, ImportStepId.SIGNALS_UNPACK)
        # A zip refused while unpacking takes its members out of the import
        files = [entry for entry in self._scan.signal_files
                 if entry.zip_member is None or entry.zip_member in unpacked]
        zip_results: Dict[Path, Dict[str, InboxImportResult]] = {}

        tracker.start_step(ImportStepId.SIGNALS_IMPORT, total=len(files))
        importer.log_header(len(files))
        for done, entry in enumerate(files):
            name = entry.path.name if entry.zip_member is None \
                else f'{entry.path.name}/{entry.zip_member.name}'
            tracker.advance(ImportStepId.SIGNALS_IMPORT, done, current_item=name)
            if entry.zip_member is None:
                importer.import_file(entry.path)
            else:
                member = entry.zip_member
                zip_results.setdefault(member.zip_path, {})[member.name] = \
                    self._import_zip_member(importer, member, unpacked[member])
            self._update_counters()
        tracker.advance(ImportStepId.SIGNALS_IMPORT, len(files))
        tracker.finish_step(ImportStepId.SIGNALS_IMPORT)
        self._settle_zips(self._scan.signal_zips, zip_results)

        tracker.start_step(ImportStepId.SIGNALS_INDEX)
        indexed = importer.rebuild_index(
            on_file_scanned=lambda done, total: tracker.advance(
                ImportStepId.SIGNALS_INDEX, done, total))
        self._update_counters()
        tracker.finish_step(ImportStepId.SIGNALS_INDEX, _status_after(indexed))

    def _run_cache_step(self) -> None:
        """
        Rebuild the discovery caches once, after every archive is written.

        Returns:
            None
        """
        tracker = self._tracker
        tracker.start_step(ImportStepId.CACHES, total=None)
        rebuilt = self._bar_importer.rebuild_discovery_caches(
            on_cache_done=lambda done, total: tracker.advance(ImportStepId.CACHES, done, total))
        self._update_counters()
        tracker.finish_step(ImportStepId.CACHES, _status_after(rebuilt))

    # =========================================================================
    # COUNTERS, SUMMARY, TIMINGS
    # =========================================================================

    def _warnings(self) -> List[str]:
        """
        Every warning the importers counted.

        Returns:
            Warning texts, ticks first
        """
        warnings: List[str] = list(self._package_warnings)
        if self._tick_importer is not None:
            warnings.extend(self._tick_importer.warnings)
        if self._bar_importer is not None:
            warnings.extend(self._bar_importer.warnings)
        if self._signal_importer is not None:
            warnings.extend(self._signal_importer.warnings)
        return warnings

    def _errors(self) -> List[str]:
        """
        Every error the importers counted, render errors included.

        Returns:
            Error texts, ticks first
        """
        errors: List[str] = list(self._package_errors)
        if self._tick_importer is not None:
            errors.extend(self._tick_importer.errors)
        errors.extend(self._zip_errors)
        if self._bar_importer is not None:
            errors.extend(self._bar_importer.errors)
        if self._signal_importer is not None:
            errors.extend(self._signal_importer.errors)
        return errors

    def _error_count(self) -> int:
        """
        How many errors the import counted.

        Returns:
            The count
        """
        return len(self._errors())

    def _update_counters(self) -> None:
        """
        Copy the importers' counters into the tracker, and pin the newest warning or error
        they counted since the last call — an error before a warning.

        Returns:
            None
        """
        ticks = self._tick_importer.total_ticks if self._tick_importer is not None else 0
        rows = self._signal_importer.total_rows if self._signal_importer is not None else 0
        self._tracker.set_written(ticks, rows)
        self._tracker.set_issue_counts(len(self._warnings()), len(self._errors()))

        # Per list, not over the joined lists: the caches write into the bar importer's
        # errors after the signal steps ran, so the joined list's last entry is not the newest
        newest: Optional[Tuple[LogLevel, str]] = None
        for key, level, entries in self._issue_lists():
            if (len(entries) > self._issues_seen.get(key, 0)
                    and (newest is None or level == LogLevel.ERROR
                         or newest[0] != LogLevel.ERROR)):
                newest = (level, entries[-1])
            self._issues_seen[key] = len(entries)
        if newest is not None:
            self._tracker.note_issue(*newest)

    def _issue_lists(self) -> List[Tuple[str, LogLevel, List[str]]]:
        """
        Every list of counted issues, in step order.

        Returns:
            (name, level, entries) per list
        """
        lists: List[Tuple[str, LogLevel, List[str]]] = [
            ('packages.warnings', LogLevel.WARNING, self._package_warnings),
            ('packages.errors', LogLevel.ERROR, self._package_errors),
        ]
        for name, importer in (('ticks', self._tick_importer), ('bars', self._bar_importer),
                               ('signals', self._signal_importer)):
            if importer is not None:
                lists.append((f'{name}.warnings', LogLevel.WARNING, importer.warnings))
                lists.append((f'{name}.errors', LogLevel.ERROR, importer.errors))
            if name == 'ticks':
                lists.append(('zips.errors', LogLevel.ERROR, self._zip_errors))
        return lists

    def _print_summary(self, outcome: ImportOutcome) -> None:
        """
        Log the import's summary: one line per archive, every warning and error.

        Args:
            outcome: How the import ended

        Returns:
            None
        """
        elapsed = time.monotonic() - self._started
        vLog.info('\n' + '=' * 80)
        vLog.info(f'IMPORT SUMMARY — {self._import_id} · {outcome.value} · '
                  f'{format_duration(elapsed)}')
        vLog.info('=' * 80)
        if self._package_fetches:
            vLog.info(f'📦 Packages: {_fetch_counts_by_service(self._package_fetches)}')
        acknowledged = [package_id for (_, package_id), (outcome, _) in self._receipts.items()
                        if outcome in (PackageReceiptOutcome.ACKNOWLEDGED,
                                       PackageReceiptOutcome.ALREADY_ACKNOWLEDGED)]
        if acknowledged:
            vLog.info(f'✅ Acknowledged: {", ".join(acknowledged)}')
        if self._by_hand.file_count():
            vLog.info(_by_hand_line(self._by_hand))
        if self._tick_importer is not None:
            empty = (f' · {self._tick_importer.empty_files} empty export(s), nothing written'
                     if self._tick_importer.empty_files else '')
            vLog.info(f'✅ Ticks:   {self._tick_importer.processed_files} file(s) processed · '
                      f'{self._tick_importer.total_ticks:,} ticks{empty}')
            for broker_type, offset_counts in self._tick_importer.get_applied_offsets().items():
                counts_str = ' · '.join(
                    f'{offset}h × {count} file(s)'
                    for offset, count in sorted(offset_counts.items()))
                vLog.info(f'✅ UTC offsets applied: {broker_type}: {counts_str}')
        if self._tick_index_run is not None:
            vLog.info(f'📚 Tick index: {_index_run_phrase(self._tick_index_run)} · '
                      f'{format_duration(self._tick_index_run.elapsed_s)}')
        for settlement in self._zip_settlements:
            vLog.info(_settlement_line(settlement))
        refused = self._refused_names()
        if refused:
            vLog.info(f'❌ Refused: {len(refused)} file(s) moved to {self._paths.refused}, '
                      'each with its reason beside it — back into the inbox by hand once fixed: '
                      + ', '.join(refused))
        if self._bar_importer is not None:
            vLog.info(f'✅ Bars:    {self._bar_importer.processed_symbols} symbol(s) · '
                      f'{self._bar_importer.total_bars_rendered:,} bars')
        if self._signal_importer is not None:
            vLog.info(f'✅ Signals: {self._signal_importer.processed_files} file(s) · '
                      f'{self._signal_importer.total_rows:,} rows · '
                      f'{self._signal_importer.moved_files} archived')
        warnings = self._warnings()
        errors = self._errors()
        vLog.info(f'⚠️  Warnings: {len(warnings)}')
        vLog.info(f'❌ Errors: {len(errors)}')
        if warnings:
            vLog.warning('\nWARNING LIST:')
            for warning in warnings:
                vLog.warning(f'  - {warning}')
        if errors:
            vLog.error('\nERROR LIST:')
            for error in errors:
                vLog.error(f'  - {error}')
        self._print_estimate_comparison()
        vLog.info('=' * 80)

    def _print_estimate_comparison(self) -> None:
        """
        Log each step's estimate at the start against what it took — how well the stored rates
        predicted this import. Nothing is printed when no step had an estimate.

        Returns:
            None
        """
        timings, total = self._tracker.estimate_comparison()
        if all(timing.estimate_s is None for timing in timings):
            return
        vLog.info('\n⏱  Estimated at the start → took:')
        for timing in timings + ([total] if total is not None else []):
            estimate = ('—' if timing.estimate_s is None
                        else f'~{format_duration(timing.estimate_s)}')
            actual = '—' if timing.actual_s is None else format_duration(timing.actual_s)
            deviation = ('' if timing.deviation_percent is None
                         else f'{timing.deviation_percent:+.0f} %')
            vLog.info(f'   {timing.label:<26} {estimate:>10}  →  {actual:<10} {deviation}')

    def _record_timings(self, outcome: ImportOutcome) -> None:
        """
        Store the step rates of a complete import into the production archive — only those
        describe the next import's work. An interrupted or scoped import stores nothing.

        Args:
            outcome: How the import ended

        Returns:
            None
        """
        if outcome not in (ImportOutcome.COMPLETED, ImportOutcome.COMPLETED_WITH_ERRORS):
            return
        if not self._writes_production:
            return
        measured = self._tracker.measured_rates()
        if measured:
            self._timings.record(measured)


def _status_after(succeeded: bool) -> ImportStepStatus:
    """
    How a step that ran to its end finished.

    Args:
        succeeded: Whether it did its work without a counted error

    Returns:
        DONE or FAILED
    """
    return ImportStepStatus.DONE if succeeded else ImportStepStatus.FAILED


def _read_tick_broker_type(path: Path) -> Optional[str]:
    """
    The broker type from the metadata block at the head of a collector file.

    Args:
        path: The tick JSON file

    Returns:
        The broker type, None when the head does not show it
    """
    try:
        with open(path, 'rb') as stream:
            head = stream.read(_HEAD_BYTES)
    except OSError:
        return None
    return _broker_type_in_head(head)


def _broker_type_in_head(head: bytes) -> Optional[str]:
    """
    The broker type from the first bytes of a collector file — a loose one or a zip member.

    Args:
        head: The file's first bytes

    Returns:
        The broker type, None when the head does not show it
    """
    match = _BROKER_TYPE_PATTERN.search(head.decode('utf-8', errors='replace'))
    return match.group(1) if match else None


def _index_run_phrase(run: TickIndexRun) -> str:
    """
    How the tick index was built, as one phrase: the method, for a full rebuild its reason, and
    what it read.

    Args:
        run: The build

    Returns:
        The phrase
    """
    if run.method is TickIndexMethod.UPDATE:
        return (f'update — {run.scanned:,} scanned · {_count_or_dash(run.unchanged)} unchanged · '
                f'{_count_or_dash(run.gone)} gone')
    reason = (_INDEX_REBUILD_REASON_TEXTS[run.rebuild_reason] if run.rebuild_reason is not None
              else 'reason not recorded')
    return f'full rebuild ({reason}) — {run.scanned:,} scanned'


def _count_or_dash(value: Optional[int]) -> str:
    """
    A count with thousands separators, or a dash where none was measured.

    Args:
        value: The count, or None

    Returns:
        The text
    """
    return '—' if value is None else f'{value:,}'


def _by_hand_line(by_hand: InboxByHand) -> str:
    """
    The summary's line on what came by hand.

    Args:
        by_hand: What the inbox held that no package service delivered now

    Returns:
        The line, e.g. "✋ By hand: 2 file(s) — 1 loose tick export, 1 zip without a manifest
        (week45.zip)"
    """
    parts = []
    if by_hand.loose_tick_files:
        parts.append(f'{by_hand.loose_tick_files} loose tick export(s)')
    if by_hand.loose_signal_files:
        parts.append(f'{by_hand.loose_signal_files} loose signal file(s)')
    if by_hand.zips_without_manifest:
        parts.append(f'{len(by_hand.zips_without_manifest)} zip(s) without a manifest '
                     f'({", ".join(by_hand.zips_without_manifest)})')
    if by_hand.zips_with_manifest:
        parts.append(f'{len(by_hand.zips_with_manifest)} package zip(s) carried in with their '
                     f'manifest ({", ".join(by_hand.zips_with_manifest)})')
    return f'✋ By hand: {by_hand.file_count()} file(s) — {", ".join(parts)}'


# The fetches whose package is due its receipt once its zip reaches the finished directory
_ACKNOWLEDGEABLE = (PackageFetchOutcome.FETCHED, PackageFetchOutcome.IN_INBOX,
                    PackageFetchOutcome.ALREADY_HERE)


def _fetch_counts_by_service(fetches: List[PackageFetch]) -> str:
    """
    What the fetch did, per package service, as one phrase.

    Args:
        fetches: Each listed package's fetch

    Returns:
        The phrase, e.g. "collector 1 fetched · rag 3 fetched, 1 in inbox", empty when nothing
        was listed
    """
    services = list(dict.fromkeys(fetch.package_service for fetch in fetches))
    return ' · '.join(
        f'{service} ' + _fetch_counts([fetch for fetch in fetches
                                       if fetch.package_service == service]).replace(' · ', ', ')
        for service in services)


def _fetch_counts(fetches: List[PackageFetch]) -> str:
    """
    What the fetch did, as one phrase.

    Args:
        fetches: Each listed package's fetch

    Returns:
        The phrase, e.g. "2 fetched · 1 already here", empty when nothing was listed
    """
    counts = [f'{sum(1 for fetch in fetches if fetch.outcome is outcome)} '
              f'{outcome.value.replace("_", " ")}'
              for outcome in PackageFetchOutcome
              if any(fetch.outcome is outcome for fetch in fetches)]
    return ' · '.join(counts)


def _member_counts(settlement: InboxZipSettlement) -> str:
    """
    How a zip's members' imports ended, as one phrase.

    Args:
        settlement: The zip's settlement

    Returns:
        The phrase, e.g. "412 imported · 3 already archived"
    """
    counts = [f'{settlement.count(outcome)} {outcome.value.replace("_", " ")}'
              for outcome in InboxImportOutcome if settlement.count(outcome)]
    return ' · '.join(counts) if counts else 'refused before its import'


def _settlement_line(settlement: InboxZipSettlement) -> str:
    """
    One summary line for a zip of the inbox: how its files' imports ended and where it went.

    Args:
        settlement: The zip's settlement

    Returns:
        The line
    """
    move = settlement.move
    if move.disposition is InboxDisposition.MOVED_TO_FINISHED:
        where, icon = f'moved to {move.destination}', '✅'
    elif move.disposition is InboxDisposition.ALREADY_THERE:
        where, icon = f'an identical copy lies in {move.destination}, the inbox copy removed', '✅'
    elif move.disposition is InboxDisposition.MOVED_TO_REFUSED:
        where, icon = f'refused, moved to {move.destination} — {settlement.reason}', '❌'
    else:
        where, icon = f'stays in the inbox — {settlement.reason}', '⚠️ '
    return f'{icon} Zip {settlement.zip_path.name}: {_member_counts(settlement)} → {where}'


def _index_row_count(index_file: Path) -> int:
    """
    Rows of an index file, read from its footer.

    Args:
        index_file: The index parquet

    Returns:
        Row count, 0 when there is no readable index
    """
    try:
        return pq.ParquetFile(index_file).metadata.num_rows
    except (OSError, ValueError):
        return 0


def _distinct_paths(index_file: Path) -> int:
    """
    Distinct data files an index describes (the signal index has one row per file and symbol).

    Args:
        index_file: The index parquet

    Returns:
        File count, 0 when there is no readable index
    """
    try:
        return int(pd.read_parquet(index_file, columns=['path'])['path'].nunique())
    except (OSError, ValueError, KeyError):
        return 0


def _ticks_and_symbols(index_file: Path, brokers: set) -> Tuple[int, int]:
    """
    Ticks and symbols the tick index holds for some broker types — the render's work, as
    known before the import adds to it.

    Args:
        index_file: The tick index parquet
        brokers: Broker types to count

    Returns:
        (tick count, symbol count), zeros without a readable index
    """
    try:
        frame = pd.read_parquet(index_file, columns=['broker_type', 'symbol', 'tick_count'])
    except (OSError, ValueError, KeyError):
        return 0, 0
    frame = frame[frame['broker_type'].isin(brokers)]
    return int(frame['tick_count'].sum()), int(frame.groupby(['broker_type', 'symbol']).ngroups)


def _ticks_and_symbols_from_index(index_manager: TickIndexManager,
                                  brokers: List[str]) -> Tuple[int, int]:
    """
    Ticks and symbols the rebuilt tick index holds for some broker types.

    Args:
        index_manager: The rebuilt tick index
        brokers: Broker types to count

    Returns:
        (tick count, symbol count)
    """
    ticks = 0
    symbols = 0
    for broker_type in brokers:
        for symbol in index_manager.list_symbols(broker_type):
            symbols += 1
            ticks += sum(int(entry.get('tick_count', 0))
                         for entry in index_manager.get_symbol_entries(broker_type, symbol))
    return ticks, symbols
