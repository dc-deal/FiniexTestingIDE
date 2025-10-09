"""
FiniexTestingIDE Tick Data Importer
===================================

Konvertiert MQL5 JSON-Exports zu optimierten Parquet-Files.
Workflow: JSON laden → Validieren → Optimieren → Parquet speichern

Author: FiniexTestingIDE Team
Version: 1.1 (with duplicate detection)
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
        Batch-Verarbeitung aller *_ticks.json Files im Source-Directory.

        Sucht alle TickCollector-Exports und konvertiert sie sequenziell.
        Fehler stoppen nicht die Verarbeitung weiterer Files.
        """
        json_files = list(self.source_dir.glob("*_ticks.json"))

        if not json_files:
            vLog.warning(f"Keine JSON-Files gefunden in {self.source_dir}")
            return

        vLog.info(f"Gefunden: {len(json_files)} JSON-Files")

        # Sequenzielle Verarbeitung mit Error-Recovery
        for json_file in json_files:
            vLog.info(f"Verarbeite: {json_file.name}")
            try:
                self.convert_json_to_parquet(json_file)
                self.processed_files += 1
            except ArtificialDuplicateException as e:
                # NEW: Special handling for duplicate detection
                error_msg = f"DUPLICATE DETECTED bei {json_file.name}"
                vLog.error(error_msg)
                vLog.error(str(e))
                self.errors.append(error_msg)
                vLog.info("→ Überspringe Import (Duplikat existiert bereits)")
            except Exception as e:
                error_msg = f"FEHLER bei {json_file.name}: {str(e)}"
                vLog.error(error_msg)
                self.errors.append(error_msg)

        self._print_summary()

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

        # Dateiname: SYMBOL_YYYYMMDD_HHMMSS.parquet
        symbol = metadata.get("symbol", "UNKNOWN")
        start_time = pd.to_datetime(metadata.get("start_time", datetime.now()))
        parquet_name = f"{symbol}_{start_time.strftime('%Y%m%d_%H%M%S')}.parquet"
        parquet_path = self.target_dir / parquet_name

        # Metadaten für Parquet-Header (alle Werte als Strings)
        parquet_metadata = {
            "source_file": json_file.name,
            "symbol": symbol,
            "broker": metadata.get("broker", "unknown"),
            "collector_version": metadata.get("collector_version", "1.0"),
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
                f"✓ {parquet_name}: {len(df):,} Ticks, "
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
        symbol: str,
        new_df: pd.DataFrame
    ) -> Optional[DuplicateReport]:
        """
        NEW: Check if a Parquet file already exists with the same source_file

        This prevents accidental re-imports and manual file duplication.
        Checks BEFORE writing the new Parquet file.

        Args:
            source_json_name: Name of the source JSON file being imported
            symbol: Trading symbol (for finding relevant Parquet files)
            new_df: DataFrame about to be written (for comparison)

        Returns:
            DuplicateReport if duplicate found, None otherwise
        """
        # Find all existing Parquet files for this symbol
        existing_files = list(self.target_dir.glob(f"{symbol}_*.parquet"))

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

                # Check if this Parquet was created from the same source JSON
                if existing_source == source_json_name:
                    # DUPLICATE DETECTED!
                    vLog.warning(
                        f"⚠️  Found existing Parquet from same source: {existing_file.name}"
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

        Transformationen:
        - float64 → float32 für Preisdaten (50% Speicher-Reduktion)
        - int64 → int32 für Volume-Daten (50% Speicher-Reduktion)
        - String → Category für Session-Daten (bessere Kompression)

        Args:
            df (pd.DataFrame): Input DataFrame

        Returns:
            pd.DataFrame: Optimierter DataFrame
        """

        # Preis-Spalten: float64 → float32 (ausreichend für Forex-Precision)
        for col in ["bid", "ask", "last", "spread_pct"]:
            if col in df.columns:
                df[col] = df[col].astype("float32")

        # Volume-Spalten: int64 → int32 (Forex-Volumes passen in int32)
        for col in ["tick_volume", "real_volume", "spread_points"]:
            if col in df.columns:
                df[col] = df[col].astype("int32")

        # Session als Category (nur wenige verschiedene Werte)
        if "session" in df.columns:
            df["session"] = df["session"].astype("category")

        return df

    def _normalize_timestamps(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Normalisiert Zeitstempel zu UTC für konsistente Verarbeitung.

        Transformationen:
        - String → datetime64[ns]
        - Timezone-unaware → UTC timezone-aware

        Args:
            df (pd.DataFrame): DataFrame mit timestamp-Spalte

        Returns:
            pd.DataFrame: DataFrame mit UTC-normalisierten Zeitstempeln
        """

        if "timestamp" in df.columns:
            # String-Zeitstempel zu datetime konvertieren
            df["timestamp"] = pd.to_datetime(df["timestamp"])

            # UTC-Timezone setzen (MQL5 liefert UTC)
            if df["timestamp"].dt.tz is None:
                df["timestamp"] = df["timestamp"].dt.tz_localize("UTC")

        return df

    def _quality_checks(self, df: pd.DataFrame) -> pd.DataFrame:
        """
        Führt Datenqualitäts-Checks durch und entfernt invalide Ticks.

        Validierungsregeln:
        - Bid/Ask > 0 (keine negativen Preise)
        - Ask >= Bid (kein invertierter Spread)
        - Spread <= 10% (Outlier-Filter)

        Args:
            df (pd.DataFrame): Input DataFrame

        Returns:
            pd.DataFrame: Bereinigter DataFrame
        """

        initial_count = len(df)

        # Kritische Preis-Validierung
        df = df[(df["bid"] > 0) & (df["ask"] > 0) & (df["ask"] >= df["bid"])]

        # Extreme Spread-Outlier entfernen (Feed-Korruption-Schutz)
        if "spread_pct" in df.columns:
            df = df[df["spread_pct"] <= 10.0]

        # Logging für entfernte Ticks
        removed = initial_count - len(df)
        if removed > 0:
            vLog.warning(
                f"Qualitäts-Check: {removed} invalide Ticks entfernt")

        return df

    def _print_summary(self):
        """
        Druckt Batch-Processing-Zusammenfassung.

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
    vLog.info("Version 1.1 - with Duplicate Detection")

    # Batch-Processing ausführen
    importer = TickDataImporter(SOURCE_DIR, TARGET_DIR)
    importer.process_all_mql5_exports()

    vLog.info("Import abgeschlossen!")
