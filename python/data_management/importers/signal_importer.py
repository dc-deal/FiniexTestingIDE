"""
FiniexTestingIDE - Signal Data Importer

Converts archived signal JSONL (#429) into columnar parquet and rebuilds the signal index.

One JSONL line = one AnalysisEnvelope + collected_msc. Each envelope explodes into one row
per (collected_msc, symbol) for present result symbols, plus one envelope-level sentinel row
(symbol = SIGNAL_ENVELOPE_SYMBOL) so every collected_msc stays resolvable for every covered
symbol — preserving the v0 provider's partial/error → defensive-HOLD behavior. Output:
<target_dir>/<pipeline_id>/<stem>.parquet, keyed by pipeline_id (= data_sentiment_type).

An imported JSONL is archived into finished_dir with its directory structure intact, so the
raw directory holds only what still needs importing. The archive is not a by-product: the
parquet is a lean projection, and the envelope's sources / metadata / errors survive nowhere
else — it stays the audit source and must remain readable.
"""

from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import pandas as pd

from python.framework.exceptions.signal_data_errors import SignalSchemaError
from python.framework.logging.bootstrap_logger import get_global_logger
from python.framework.signal_data.signal_jsonl_loader import load_signal_series
from python.framework.types.signal_data_types import (
    SIGNAL_ENVELOPE_SYMBOL, SentimentResult, SignalParquetColumn, SignalSnapshot)
from python.data_management.index.signal_index_manager import SignalIndexManager

vLog = get_global_logger()


def _epoch_ms(moment: Optional[datetime]) -> Optional[int]:
    """
    Render a datetime as epoch milliseconds for a parquet column.

    Args:
        moment: A tz-aware datetime, or None

    Returns:
        Epoch-ms int, or None when the moment is absent — never a placeholder, so
        "absent" stays distinguishable from zero
    """
    return None if moment is None else int(moment.timestamp() * 1000)


class SignalDataImporter:
    """
    Converts archived signal JSONL into columnar parquet (#429).

    Args:
        source_dir: Raw signal JSONL directory (e.g. data/raw/signals)
        target_dir: Parquet output root (e.g. data/processed/signals)
        override: Overwrite an existing parquet — and re-read the finished archive
        finished_dir: Archive for imported JSONL; None disables the move
    """

    VERSION = "1.0"

    def __init__(self, source_dir: str, target_dir: str, override: bool = False,
                 finished_dir: Optional[str] = None):
        self.source_dir = Path(source_dir)
        self.target_dir = Path(target_dir)
        self.target_dir.mkdir(parents=True, exist_ok=True)

        self.override = override
        self.finished_dir = Path(finished_dir) if finished_dir else None

        # Import statistics
        self.processed_files = 0
        self.total_rows = 0
        self.moved_files = 0
        self.pruned_dirs = 0
        self.errors: List[str] = []
        self.warnings: List[str] = []

    def process_all_signals(self) -> None:
        """
        Convert every *.jsonl of the inbox, then rebuild the index.
        Errors do not stop processing of remaining files.
        """
        jsonl_files = self._collect_files()

        if not jsonl_files:
            vLog.warning(
                f"No signal JSONL found in {self.source_dir}. Rebuilding index only.")
            self._prune_empty_source_dirs()
            self._rebuild_index()
            return

        vLog.info("\n" + "=" * 80)
        vLog.info(f"FiniexTestingIDE Signal Data Importer V{self.VERSION}")
        vLog.info("=" * 80)
        vLog.info(f"Found: {len(jsonl_files)} JSONL file(s)")
        vLog.info(f"Override Mode: {'ENABLED' if self.override else 'DISABLED'}")
        vLog.info("=" * 80 + "\n")

        for root, jsonl_file in jsonl_files:
            vLog.info(f"\n📄 Processing: {jsonl_file.name}")
            try:
                written = self.convert_jsonl_to_parquet(jsonl_file)
                self.processed_files += 1
                if written is not None:
                    self._move_to_finished(root, jsonl_file)
            except Exception as e:
                error_msg = f"ERROR in {jsonl_file.name}: {str(e)}"
                vLog.error(error_msg)
                self.errors.append(error_msg)

        self._prune_empty_source_dirs()
        self._rebuild_index()
        self._print_summary()

    def _collect_files(self) -> List[Tuple[Path, Path]]:
        """
        The files to import, each paired with the root it was found under.

        The inbox is source_dir. In override mode the finished archive is read as
        well: "override" means rebuilding what is already imported, and once a file
        has been imported that is exactly where it lives. A relative path present in
        both roots is taken from source_dir — a re-exported day supersedes its
        archived copy, and the subsequent move overwrites it.

        Returns:
            (root, file) pairs, sorted by relative path
        """
        roots = [self.source_dir]
        if self.override and self.finished_dir and self.finished_dir.exists():
            roots.append(self.finished_dir)

        found: Dict[Path, Tuple[Path, Path]] = {}
        for root in roots:
            for path in root.rglob("*.jsonl"):
                found.setdefault(path.relative_to(root), (root, path))

        return [found[key] for key in sorted(found)]

    def _move_to_finished(self, root: Path, jsonl_file: Path) -> None:
        """
        Archive an imported JSONL, preserving its directory structure.

        The path is kept relative to the root it came from, NOT rebuilt from the
        resolved pipeline_id: a file sitting in a folder that does not match its own
        pipeline_id is an anomaly, and normalizing it here would silently repair it
        instead of leaving it visible.

        Args:
            root: The directory the file was found under
            jsonl_file: The imported JSONL
        """
        if self.finished_dir is None:
            return

        target = self.finished_dir / jsonl_file.relative_to(root)
        if target == jsonl_file:
            return

        target.parent.mkdir(parents=True, exist_ok=True)
        jsonl_file.replace(target)
        self.moved_files += 1
        vLog.info(f"   → archived to {target}")

    def _prune_empty_source_dirs(self) -> None:
        """
        Remove inbox folders left empty after their files were archived.

        The inbox mirrors the pipeline structure (source_dir/<pipeline_id>/), and a
        folder whose files have all moved to the archive is leftover scaffolding —
        it makes an emptied inbox look occupied. Removal is by rmdir, which refuses
        a non-empty directory, so a folder still holding anything survives by
        construction. The inbox root itself is kept.
        """
        if not self.source_dir.exists():
            return

        # Reverse-sorted paths put children before their parents, so a nested
        # empty tree collapses in one pass.
        for path in sorted(self.source_dir.rglob('*'), reverse=True):
            if not path.is_dir():
                continue
            try:
                path.rmdir()
            except OSError:
                continue
            self.pruned_dirs += 1
            vLog.info(
                f"   🧹 Removed empty inbox folder: {path.relative_to(self.source_dir)}")

    def convert_jsonl_to_parquet(self, jsonl_file: Path) -> Optional[Path]:
        """
        Convert one signal JSONL file to a columnar parquet.

        Args:
            jsonl_file: Archived signal JSONL path

        Returns:
            The written parquet path, or None if the file held no snapshots
        """
        # Reuse the validated parse (schema_version gate + time order)
        snapshots = load_signal_series(jsonl_file, signal_kind='').snapshots
        if not snapshots:
            vLog.warning(f"No snapshots in {jsonl_file.name}")
            return None

        pipeline_id = self._resolve_pipeline_id(snapshots, jsonl_file)
        self._validate_stream_identity(snapshots, jsonl_file)
        rows = self._explode(snapshots)
        df = pd.DataFrame(rows, columns=[c.value for c in SignalParquetColumn])

        target_path = self.target_dir / pipeline_id / f"{jsonl_file.stem}.parquet"
        target_path.parent.mkdir(parents=True, exist_ok=True)
        if target_path.exists() and not self.override:
            raise FileExistsError(
                f"{target_path} exists (use override to replace)")
        df.to_parquet(target_path, index=False)

        self.total_rows += len(df)
        symbols = sorted(
            {r[SignalParquetColumn.SYMBOL.value] for r in rows} - {SIGNAL_ENVELOPE_SYMBOL})
        vLog.info(
            f"✅ {jsonl_file.name} → {target_path.relative_to(self.target_dir)} "
            f"({len(df)} rows; symbols: {', '.join(symbols)})")
        return target_path

    def _resolve_pipeline_id(self, snapshots: List[SignalSnapshot], jsonl_file: Path) -> str:
        """Derive the output folder key (= data_sentiment_type) from the envelope pipeline_id."""
        ids = {s.pipeline_id for s in snapshots if s.pipeline_id}
        if not ids:
            raise SignalSchemaError(
                f"{jsonl_file.name}: no 'pipeline_id' in any envelope — cannot key the source.")
        if len(ids) > 1:
            raise SignalSchemaError(
                f"{jsonl_file.name}: mixed 'pipeline_id' values {sorted(ids)} in one file.")
        return ids.pop()

    def _validate_stream_identity(
        self, snapshots: List[SignalSnapshot], jsonl_file: Path
    ) -> None:
        """
        Check the stream identity of a file: epoch monotonicity (hard) and seq contiguity (soft).

        A REPEATED epoch is refused. seq is unique only WITHIN an epoch, so two series
        carrying the same epoch would silently merge under the (pipeline_id, stream_epoch,
        seq) key — the exact failure the identity exists to prevent. A seq HOLE is reported
        and imported: the file is incomplete, not wrong, and refusing it would discard the
        envelopes we do have. Lines without a stream identity (the pre-stream era) are
        unverifiable, which is a distinct state from verified-contiguous and is not an error.

        Args:
            snapshots: Parsed snapshots of one file
            jsonl_file: The file, for messages

        Raises:
            SignalSchemaError: If an epoch reappears after a later one was seen
        """
        identified = [s for s in snapshots if s.seq is not None
                      and s.stream_epoch is not None]
        if not identified:
            return

        # Snapshots arrive in time order (the loader sorts them), so both violations below
        # are "went backwards in time" — which is exactly what a rewound series looks like
        # from the outside.
        highest_epoch = identified[0].stream_epoch
        last_seq: Dict[int, int] = {}
        seqs_per_epoch: Dict[int, List[int]] = {}
        for snapshot in identified:
            epoch, seq = snapshot.stream_epoch, snapshot.seq
            if epoch < highest_epoch:
                raise SignalSchemaError(
                    f"{jsonl_file.name}: stream_epoch went backwards ({highest_epoch} → "
                    f"{epoch}) — the producer's series was rewound and two series would "
                    f"merge under one key. Refusing the file."
                )
            if epoch in last_seq and seq < last_seq[epoch]:
                raise SignalSchemaError(
                    f"{jsonl_file.name}: seq went backwards within epoch {epoch} "
                    f"({last_seq[epoch]} → {seq}) — the epoch was reissued for a second "
                    f"series. seq is unique only within an epoch, so refusing the file."
                )
            highest_epoch = epoch
            last_seq[epoch] = seq
            seqs_per_epoch.setdefault(epoch, []).append(seq)

        for epoch, seqs in seqs_per_epoch.items():
            holes = sum(b - a - 1 for a, b in zip(seqs, seqs[1:]) if b - a > 1)
            if holes:
                message = (f"{jsonl_file.name}: {holes} missing seq in epoch {epoch} "
                           f"({seqs[0]}→{seqs[-1]}) — envelopes were never received")
                vLog.warning(f"   ⚠️ {message}")
                self.errors.append(message)

    def _explode(self, snapshots: List[SignalSnapshot]) -> List[Dict]:
        """One row per (collected_msc, symbol) + one envelope-level sentinel row each."""
        rows: List[Dict] = []
        for snap in snapshots:
            msc = int(snap.collected_msc.timestamp() * 1000)
            envelope = {
                SignalParquetColumn.STATUS.value: snap.status,
                SignalParquetColumn.SCHEMA_VERSION.value: snap.schema_version,
                SignalParquetColumn.PIPELINE_ID.value: snap.pipeline_id,
                SignalParquetColumn.PROMPT_VERSION.value: snap.prompt_version,
                SignalParquetColumn.PROMPT_ID.value: snap.prompt_id,
                SignalParquetColumn.PROMPT_HASH.value: snap.prompt_hash,
                SignalParquetColumn.DATA_ORIGIN.value: snap.data_origin,
                SignalParquetColumn.CONFIG_FINGERPRINT.value: snap.config_fingerprint,
                # Lives in metadata, not top-level. Missing key and null collapse to the
                # same '' = unknown state — never to 'scheduled' (a boot pass would be
                # mislabelled as a grid point).
                # Top-level since the producer promoted it out of metadata; the model lifts
                # the legacy metadata location on read, so both eras land here. Missing and
                # null collapse to '' = unknown — never to 'scheduled', which would mislabel
                # a boot pass as a grid point.
                SignalParquetColumn.TRIGGER_REASON.value: snap.trigger_reason,
                # Stream identity (#141 Part 2a) — absent for every line written before the
                # stream contract, a state the reader distinguishes from zero.
                SignalParquetColumn.SEQ.value: snap.seq,
                SignalParquetColumn.STREAM_EPOCH.value: snap.stream_epoch,
                SignalParquetColumn.AVAILABLE_MSC.value: _epoch_ms(snap.available_msc),
            }
            # Envelope sentinel row (symbol = '*') — keeps this collected_msc resolvable
            # for every covered symbol even when the envelope omits it (partial/error).
            rows.append(self._row(msc, SIGNAL_ENVELOPE_SYMBOL, None, envelope))
            for result in snap.result:
                rows.append(self._row(msc, result.symbol, result, envelope))
        return rows

    def _row(self, msc: int, symbol: str,
             result: Optional[SentimentResult], envelope: Dict) -> Dict:
        """Build one parquet row (envelope sentinel when result is None)."""
        row = {c.value: '' for c in SignalParquetColumn}
        row.update(envelope)
        row[SignalParquetColumn.COLLECTED_MSC.value] = msc
        row[SignalParquetColumn.SYMBOL.value] = symbol
        if result is None:
            row[SignalParquetColumn.SIGNAL.value] = ''
            row[SignalParquetColumn.SENTIMENT_SCORE.value] = 0.0
            row[SignalParquetColumn.CONFIDENCE.value] = 0.0
            row[SignalParquetColumn.REASONING.value] = ''
            row[SignalParquetColumn.URGENCY.value] = 0.0
            row[SignalParquetColumn.IS_BREAKING.value] = False
            row[SignalParquetColumn.BASIS.value] = ''
            row[SignalParquetColumn.EVIDENCE_AS_OF.value] = None
        else:
            row[SignalParquetColumn.SIGNAL.value] = result.signal
            row[SignalParquetColumn.SENTIMENT_SCORE.value] = result.sentiment_score
            row[SignalParquetColumn.CONFIDENCE.value] = result.confidence
            row[SignalParquetColumn.REASONING.value] = result.reasoning
            row[SignalParquetColumn.URGENCY.value] = result.urgency
            row[SignalParquetColumn.IS_BREAKING.value] = result.is_breaking
            row[SignalParquetColumn.BASIS.value] = result.basis
            row[SignalParquetColumn.EVIDENCE_AS_OF.value] = _epoch_ms(
                result.evidence_as_of)
        return row

    def _rebuild_index(self) -> None:
        """Rebuild the signal index over the target directory."""
        try:
            index_manager = SignalIndexManager(data_dir=str(self.target_dir))
            index_manager.build_index(force_rebuild=True)
            vLog.info("✅ Signal index rebuilt")
        except Exception as e:
            vLog.error(f"Signal index rebuild failed: {e}")

    def _print_summary(self) -> None:
        """Print the import summary."""
        vLog.info("\n" + "=" * 80)
        vLog.info(
            f"Signal Import Summary: {self.processed_files} file(s), {self.total_rows} rows")
        if self.moved_files:
            vLog.info(f"Archived to {self.finished_dir}: {self.moved_files} file(s)")
        if self.pruned_dirs:
            vLog.info(f"Emptied inbox folders removed: {self.pruned_dirs}")
        if self.warnings:
            vLog.info(f"Warnings: {len(self.warnings)}")
        if self.errors:
            vLog.info(f"Errors: {len(self.errors)}")
        vLog.info("=" * 80)
