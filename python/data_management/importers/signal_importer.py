"""
FiniexTestingIDE - Signal Data Importer

Converts archived signal JSONL (#429) into columnar parquet and rebuilds the signal index.

One JSONL line = one AnalysisEnvelope + collected_msc. Each envelope explodes into one row
per (collected_msc, symbol) for present result symbols, plus one envelope-level sentinel row
(symbol = SIGNAL_ENVELOPE_SYMBOL) so every collected_msc stays resolvable for every covered
symbol — preserving the v0 provider's partial/error → defensive-HOLD behavior. Output:
<target_dir>/<pipeline_id>/<stem>.parquet, keyed by pipeline_id (= data_sentiment_type).
"""

from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

from python.framework.exceptions.signal_data_errors import SignalSchemaError
from python.framework.logging.bootstrap_logger import get_global_logger
from python.framework.signal_data.signal_jsonl_loader import load_signal_series
from python.framework.types.signal_data_types import (
    SIGNAL_ENVELOPE_SYMBOL, SentimentResult, SignalParquetColumn, SignalSnapshot)
from python.data_management.index.signal_index_manager import SignalIndexManager

vLog = get_global_logger()


class SignalDataImporter:
    """
    Converts archived signal JSONL into columnar parquet (#429).

    Args:
        source_dir: Raw signal JSONL directory (e.g. data/raw/signals)
        target_dir: Parquet output root (e.g. data/processed/signals)
        override: Overwrite an existing parquet
    """

    VERSION = "1.0"

    def __init__(self, source_dir: str, target_dir: str, override: bool = False):
        self.source_dir = Path(source_dir)
        self.target_dir = Path(target_dir)
        self.target_dir.mkdir(parents=True, exist_ok=True)

        self.override = override

        # Import statistics
        self.processed_files = 0
        self.total_rows = 0
        self.errors: List[str] = []
        self.warnings: List[str] = []

    def process_all_signals(self) -> None:
        """
        Find every *.jsonl under source_dir, convert each, then rebuild the index.
        Errors do not stop processing of remaining files.
        """
        jsonl_files = sorted(self.source_dir.rglob("*.jsonl"))

        if not jsonl_files:
            vLog.warning(
                f"No signal JSONL found in {self.source_dir}. Rebuilding index only.")
            self._rebuild_index()
            return

        vLog.info("\n" + "=" * 80)
        vLog.info(f"FiniexTestingIDE Signal Data Importer V{self.VERSION}")
        vLog.info("=" * 80)
        vLog.info(f"Found: {len(jsonl_files)} JSONL file(s)")
        vLog.info(f"Override Mode: {'ENABLED' if self.override else 'DISABLED'}")
        vLog.info("=" * 80 + "\n")

        for jsonl_file in jsonl_files:
            vLog.info(f"\n📄 Processing: {jsonl_file.name}")
            try:
                self.convert_jsonl_to_parquet(jsonl_file)
                self.processed_files += 1
            except Exception as e:
                error_msg = f"ERROR in {jsonl_file.name}: {str(e)}"
                vLog.error(error_msg)
                self.errors.append(error_msg)

        self._rebuild_index()
        self._print_summary()

    def convert_jsonl_to_parquet(self, jsonl_file: Path) -> Optional[Path]:
        """
        Convert one signal JSONL file to a columnar parquet.

        Args:
            jsonl_file: Archived signal JSONL path

        Returns:
            The written parquet path, or None if the file held no snapshots
        """
        # Reuse the validated parse (schema_version gate + time order)
        snapshots = load_signal_series(jsonl_file, source='').snapshots
        if not snapshots:
            vLog.warning(f"No snapshots in {jsonl_file.name}")
            return None

        pipeline_id = self._resolve_pipeline_id(snapshots, jsonl_file)
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
        else:
            row[SignalParquetColumn.SIGNAL.value] = result.signal
            row[SignalParquetColumn.SENTIMENT_SCORE.value] = result.sentiment_score
            row[SignalParquetColumn.CONFIDENCE.value] = result.confidence
            row[SignalParquetColumn.REASONING.value] = result.reasoning
            row[SignalParquetColumn.URGENCY.value] = result.urgency
            row[SignalParquetColumn.IS_BREAKING.value] = result.is_breaking
            row[SignalParquetColumn.BASIS.value] = result.basis
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
        if self.warnings:
            vLog.info(f"Warnings: {len(self.warnings)}")
        if self.errors:
            vLog.info(f"Errors: {len(self.errors)}")
        vLog.info("=" * 80)
