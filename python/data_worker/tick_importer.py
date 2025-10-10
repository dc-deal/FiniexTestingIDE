"""
FiniexTestingIDE Tick Data Importer
===================================

Konvertiert MQL5 JSON-Exports zu optimierten Parquet-Files.
Workflow: JSON laden → Validieren → Optimieren → Parquet speichern

Author: FiniexTestingIDE Team
Version: 1.2 (with hierarchical directory structure)
"""

import json
from python.components.logger.bootstrap_logger import setup_logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

from python.configuration import AppConfigLoader
from python.components.logger.bootstrap_logger import setup_logging

# NEW: Import duplicate detection
from python.data_worker.data_loader.exceptions import (
    ArtificialDuplicateException,
    DuplicateReport
)

setup_logging(name="StrategyRunner")
vLog = setup_logging(name="StrategyRunner")


class TickDataImporter:
    """
    Konvertiert MQL5 JSON-Exports zu Parquet-Format.

    Hauptfunktionen:
    - JSON → Parquet Konvertierung (10:1 Kompression)
    - Datentyp-Optimierung für Performance
    - Qualitäts-Checks und Bereinigung
    - Batch-Verarbeitung mit Error-Handling
    - Duplicate Prevention (NEW in V1.1)
    - Hierarchical directory structure (NEW in V1.2)

    Args:
        source_dir (str): Verzeichnis mit JSON-Files
        target_dir (str): Zielverzeichnis für Parquet-Output
    """

    def __init__(self, source_dir: str, target_dir: str):
        """
        Initialisiert Importer mit Source- und Target-Pfaden.

        Args:
            source_dir (str): MQL5 JSON-Export-Verzeichnis
            target_dir (str): Parquet-Zielverzeichnis
        """
        self.source_dir = Path(source_dir)
        self.target_dir = Path(target_dir)
        self.target_dir.mkdir(parents=True, exist_ok=True)

        # Batch-Processing-Statistiken
        self.processed_files = 0
        self.total_ticks = 0
        self.errors = []
        self.appConfig = AppConfigLoader()

    def process_all_mql5_exports(self):
        """
        Sucht alle TickCollector-Exports und konvertiert sie sequenziell.
        Fehler stoppen nicht die Verarbeitung weiterer Files.

        EXTENDED (C#002): Rebuilds index after successful imports
        """
        json_files = list(self.source_dir.glob("*_ticks.json"))

        if not json_files:
            vLog.warning(
                f"Keine JSON-Files gefunden in {self.source_dir}. Just rebuilding Index.")
            self.rebuild_parquet_index()
            return

        vLog.info(f"Gefunden: {len(json_files)} JSON-Files")

        # Sequenzielle Verarbeitung mit Error-Recovery
        for json_file in json_files:
            vLog.info(f"Verarbeite: {json_file.name}")
            try:
                self.convert_json_to_parquet(json_file)
                self.processed_files += 1
            except ArtificialDuplicateException as e:
                # Special handling for duplicate detection
                error_msg = f"DUPLICATE DETECTED bei {json_file.name}"
                vLog.error(error_msg)
                vLog.error(str(e))
                self.errors.append(error_msg)
                vLog.info("→ Überspringe Import (Duplikat existiert bereits)")
            except Exception as e:
                error_msg = f"FEHLER bei {json_file.name}: {str(e)}"
                vLog.error(error_msg)
                self.errors.append(error_msg)

        self.rebuild_parquet_index()

        self._print_summary()

    def rebuild_parquet_index(self):
        # NEW (C#002): Rebuild index after successful imports
        vLog.info("🔄 Rebuilding Parquet index...")
        try:
            from python.data_worker.data_loader.parquet_index import ParquetIndexManager

            index_manager = ParquetIndexManager(self.target_dir)
            index_manager.build_index(force_rebuild=True)

            # Print brief summary
            symbols = index_manager.list_symbols()
            vLog.info(f"✅ Index rebuilt: {len(symbols)} symbols indexed")

        except Exception as e:
            vLog.error(f"❌ Failed to rebuild index: {e}")
            vLog.error("   Index may be outdated - run manual rebuild!")

    def convert_json_to_parquet(self, json_file: Path):
        """
        Konvertiert einzelne JSON-Datei zu optimiertem Parquet.

        Pipeline:
        1. JSON laden und Struktur validieren
        2. DataFrame erstellen und Datentypen optimieren
        3. Zeitstempel normalisieren und Qualitäts-Checks
        4. NEW: Check for existing duplicates BEFORE writing
        5. Als Parquet mit Metadaten speichern
        6. Optional: Source-File nach finished/ verschieben

        Args:
            json_file (Path): Pfad zur JSON-Datei

        Raises:
            ValueError: Bei ungültiger JSON-Struktur
            ArtificialDuplicateException: Bei existierenden Duplikaten
            Exception: Bei Parquet-Schreibfehlern
        """

        # ===========================================
        # 1. JSON LADEN UND VALIDIEREN
        # ===========================================

        with open(json_file, "r", encoding="utf-8") as f:
            data = json.load(f)

        # TickCollector-Struktur prüfen: {metadata: {...}, ticks: [...]}
        if "ticks" not in data or "metadata" not in data:
            raise ValueError(
                "Ungültige JSON-Struktur - 'ticks' oder 'metadata' fehlt")

        ticks = data["ticks"]
        metadata = data["metadata"]

        # Early exit bei leeren Tick-Arrays
        if not ticks:
            vLog.warning(f"Keine Ticks in {json_file.name}")
            return

        # ===========================================
        # 2. DATAFRAME ERSTELLEN UND OPTIMIEREN
        # ===========================================

        df = pd.DataFrame(ticks)

        # Datentyp-Optimierung: float64→float32, int64→int32
        df = self._optimize_datatypes(df)

        # Zeitstempel-Normalisierung zu UTC
        df = self._normalize_timestamps(df)

        # Qualitäts-Checks: Invalid prices, extreme spreads
        df = self._quality_checks(df)

        # Chronologische Sortierung (essentiell für Testing-Engine)
        df = df.sort_values("timestamp").reset_index(drop=True)

        # Duplikat-Handling auskommentiert (Ticks as-is verarbeiten)
        # initial_count = len(df)
        # df = df.drop_duplicates(subset=['time_msc'], keep='last')

        # ===========================================
        # 3. PARQUET-OUTPUT MIT METADATEN
        # ===========================================

        # NEW (C#003): Extract data_collector with fallback to "mt5"
        data_collector = metadata.get("data_collector", "mt5")

        symbol = metadata.get("symbol", "UNKNOWN")
        start_time = pd.to_datetime(metadata.get("start_time", datetime.now()))

        # NEW (C#003): Hierarchical directory structure
        # data/processed/{data_collector}/{symbol}/
        target_path = self.target_dir / data_collector / symbol
        target_path.mkdir(parents=True, exist_ok=True)

        # Dateiname: SYMBOL_YYYYMMDD_HHMMSS.parquet
        parquet_name = f"{symbol}_{start_time.strftime('%Y%m%d_%H%M%S')}.parquet"
        parquet_path = target_path / parquet_name

        # Metadaten für Parquet-Header (alle Werte als Strings)
        parquet_metadata = {
            "source_file": json_file.name,
            "symbol": symbol,
            "broker": metadata.get("broker", "unknown"),
            "collector_version": metadata.get("collector_version", "1.0"),
            "data_collector": data_collector,  # NEW (C#003)
            "processed_at": datetime.now().isoformat(),
            "tick_count": str(
                len(df)
            ),  # Wichtig: als String für Parquet-Kompatibilität
        }

        # ===========================================
        # 4. NEW: CHECK FOR EXISTING DUPLICATES
        # ===========================================

        vLog.debug(f"Checking for existing duplicates of {json_file.name}...")
        duplicate_report = self._check_for_existing_duplicate(
            json_file.name,
            data_collector,  # CHANGED (C#003): Pass data_collector
            symbol,
            df
        )

        if duplicate_report:
            # DUPLICATE DETECTED - Abort import!
            raise ArtificialDuplicateException(duplicate_report)

        # ===========================================
        # 5. PARQUET SCHREIBEN UND FILE-MANAGEMENT
        # ===========================================

        try:
            # Arrow-Table mit Metadaten erstellen
            table = pa.Table.from_pandas(df)
            table = table.replace_schema_metadata(parquet_metadata)
            pq.write_table(table, parquet_path, compression="snappy")

            # Kompressionsrate berechnen (BEVOR File moved wird)
            json_size = json_file.stat().st_size
            parquet_size = parquet_path.stat().st_size
            compression_ratio = json_size / parquet_size if parquet_size > 0 else 0

            # Optional: Source-File nach finished/ verschieben
            if self.appConfig.get_move_processed_files:
                finished_dir = Path("./data/finished/")
                finished_dir.mkdir(exist_ok=True)
                finished_file = finished_dir / json_file.name
                json_file.rename(finished_file)
                vLog.info(f"→ Moved {json_file.name} to finished/")
            else:
                vLog.info(
                    f"→ DEV_MODE: File bleibt in raw/ (MOVE_PROCESSED_FILES=false)"
                )

            self.total_ticks += len(df)

            # Success-Logging mit Statistiken
            vLog.info(
                # CHANGED (C#003): Show new path
                f"✓ {data_collector}/{symbol}/{parquet_name}: {len(df):,} Ticks, "
                f"Kompression {compression_ratio:.1f}:1 "
                f"({json_size/1024/1024:.1f}MB → {parquet_size/1024/1024:.1f}MB)"
            )

        except Exception as e:
            # Detailliertes Error-Logging für Debugging
            vLog.error(f"FEHLER beim Schreiben von {parquet_path}")
            vLog.error(f"Original Error: {str(e)}")
            vLog.error(f"Error Type: {type(e)}")
            vLog.error(f"Metadaten waren: {parquet_metadata}")
            # Metadaten-Typ-Debugging
            for key, value in parquet_metadata.items():
                vLog.error(f"  {key}: {value} (Type: {type(value)})")
            raise  # Re-raise für Caller-Error-Handling

    def _check_for_existing_duplicate(
        self,
        source_json_name: str,
        data_collector: str,
        symbol: str,
        new_df: pd.DataFrame
    ) -> Optional[DuplicateReport]:
        """
        Check if a Parquet file already exists with the same source_file

        This prevents accidental re-imports and manual file duplication.
        Checks BEFORE writing the new Parquet file.

        EXTENDED (C#003b): Searches across ALL data_collector directories
        to detect duplicates even if imported under different collector.

        Args:
            source_json_name: Name of the source JSON file being imported
            data_collector: Data collector type being imported to (e.g. "mt5")
            symbol: Trading symbol (for finding relevant Parquet files)
            new_df: DataFrame about to be written (for comparison)

        Returns:
            DuplicateReport if duplicate found, None otherwise
        """
        # CHANGED (C#003b): Search across ALL data_collector directories
        # Pattern: data/processed/*/SYMBOL/SYMBOL_*.parquet
        search_pattern = f"*/{symbol}/{symbol}_*.parquet"
        existing_files = list(self.target_dir.glob(search_pattern))

        if not existing_files:
            return None  # No existing files, safe to proceed

        # Check each existing file's metadata
        for existing_file in existing_files:
            try:
                parquet_file = pq.ParquetFile(existing_file)
                metadata_raw = parquet_file.metadata.metadata

                # Extract and decode all metadata
                existing_metadata = {
                    key.decode('utf-8') if isinstance(key, bytes) else key:
                    value.decode('utf-8') if isinstance(value,
                                                        bytes) else value
                    for key, value in metadata_raw.items()
                }

                # Extract source_file from metadata
                existing_source = existing_metadata.get('source_file', '')
                existing_collector = existing_metadata.get(
                    'data_collector', 'unknown')

                # Check if this Parquet was created from the same source JSON
                if existing_source == source_json_name:
                    # DUPLICATE DETECTED!
                    # Extract directory path to show data_collector location
                    relative_path = existing_file.relative_to(self.target_dir)
                    collector_path = relative_path.parts[0] if len(
                        relative_path.parts) > 0 else "unknown"

                    vLog.warning(
                        f"⚠️  Found existing Parquet from same source: {collector_path}/{symbol}/{existing_file.name}"
                    )
                    vLog.warning(
                        f"    Existing: data_collector='{existing_collector}' | Importing: data_collector='{data_collector}'"
                    )

                    # Read existing file data for comparison
                    existing_df = pd.read_parquet(existing_file)

                    # Build comparison report - only show existing file
                    return DuplicateReport(
                        source_file=source_json_name,
                        duplicate_files=[existing_file],
                        tick_counts=[len(existing_df)],
                        time_ranges=[
                            (existing_df['timestamp'].min(),
                             existing_df['timestamp'].max())
                        ],
                        file_sizes_mb=[
                            existing_file.stat().st_size / (1024 * 1024)
                        ],
                        metadata=[existing_metadata]
                    )

            except Exception as e:
                vLog.warning(
                    f"Could not read metadata from {existing_file.name}: {e}")
                # Continue checking other files

        return None  # No duplicates found

    def _optimize_datatypes(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Optimiert DataFrame-Datentypen für Performance und Speichernutzung.

        Konvertierungen:
        - float64 → float32 (für bid/ask/last)
        - int64 → int32 (für Flags, Volumes)

        Args:
            df (pd.DataFrame): Raw DataFrame

        Returns:
            pd.DataFrame: Optimized DataFrame
        """
        # Float-Spalten zu float32
        float_cols = ["bid", "ask", "last", "spread_pct", "real_volume"]
        for col in float_cols:
            if col in df.columns:
                df[col] = df[col].astype("float32")

        # Int-Spalten zu int32
        int_cols = [
            "tick_volume",
            "chart_tick_volume",
            "spread_points",
        ]
        for col in int_cols:
            if col in df.columns:
                df[col] = df[col].astype("int32")

        return df

    def _normalize_timestamps(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Normalisiert Zeitstempel zu UTC datetime64[ns].

        Args:
            df (pd.DataFrame): DataFrame mit 'timestamp' Spalte

        Returns:
            pd.DataFrame: DataFrame mit normalisiertem Zeitstempel
        """
        if "timestamp" in df.columns:
            df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)

        return df

    def _quality_checks(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Führt Qualitäts-Checks auf Tick-Daten durch.

        Checks:
        - Bid/Ask > 0 (keine invaliden Preise)
        - Spread < 5% (keine extremen Spreads)
        - Consecutive price jumps < 10% (keine Flash-Crashes)

        Invalide Ticks werden NICHT entfernt, nur geloggt!

        Args:
            df (pd.DataFrame): Raw DataFrame

        Returns:
            pd.DataFrame: Validated DataFrame (mit Warnings)
        """
        initial_count = len(df)

        # Check 1: Invalid Prices (bid/ask <= 0)
        invalid_prices = df[(df["bid"] <= 0) | (df["ask"] <= 0)]
        if len(invalid_prices) > 0:
            vLog.warning(
                f"⚠️  {len(invalid_prices)} Ticks mit invaliden Preisen gefunden (bid/ask <= 0)"
            )

        # Check 2: Extreme Spreads (>5%)
        if "spread_pct" in df.columns:
            extreme_spreads = df[df["spread_pct"] > 5.0]
            if len(extreme_spreads) > 0:
                vLog.warning(
                    f"⚠️  {len(extreme_spreads)} Ticks mit extremen Spreads (>5%) gefunden"
                )

        # Check 3: Price Jumps (>10% bid change between consecutive ticks)
        df["bid_pct_change"] = df["bid"].pct_change().abs() * 100
        large_jumps = df[df["bid_pct_change"] > 10.0]
        if len(large_jumps) > 0:
            vLog.warning(
                f"⚠️  {len(large_jumps)} Ticks mit großen Preissprüngen (>10%) gefunden"
            )

        # Cleanup temporary columns
        df = df.drop(columns=["bid_pct_change"], errors="ignore")

        return df

    def _print_summary(self):
        """
        Gibt Zusammenfassung der Batch-Verarbeitung aus.

        Ausgabe:
        - Anzahl verarbeiteter Dateien
        - Gesamtzahl konvertierter Ticks
        - Aufgetretene Fehler (falls vorhanden)
        """
        vLog.info("=" * 50)
        vLog.info("VERARBEITUNGS-ZUSAMMENFASSUNG")
        vLog.info("=" * 50)
        vLog.info(f"Verarbeitete Dateien: {self.processed_files}")
        vLog.info(f"Gesamte Ticks: {self.total_ticks:,}")
        vLog.info(f"Fehler: {len(self.errors)}")

        # Detaillierte Fehler-Liste
        if self.errors:
            vLog.error("FEHLER-LISTE:")
            for error in self.errors:
                vLog.error(f"  - {error}")


# ===========================================
# CLI-INTERFACE
# ===========================================

if __name__ == "__main__":
    """
    Command-Line Interface für Standalone-Ausführung.

    Konvertiert alle JSON-Files aus ./data/raw/ zu Parquet in ./data/processed/
    """

    # Standard-Pfade (TODO: Als CLI-Args konfigurierbar machen)
    SOURCE_DIR = "./data/raw/"  # MQL5 Export-Ordner
    TARGET_DIR = "./data/processed/"  # Parquet-Ziel

    vLog.info("FiniexTestingIDE Tick Data Importer gestartet")
    vLog.info("Version 1.2 - with Hierarchical Directory Structure")

    # Batch-Processing ausführen
    importer = TickDataImporter(SOURCE_DIR, TARGET_DIR)
    importer.process_all_mql5_exports()

    vLog.info("Import abgeschlossen!")
