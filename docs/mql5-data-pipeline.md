# MQL5 → Testing IDE Daten-Pipeline

## Übersicht

Diese Pipeline sammelt Live-Tick-Daten aus MetaTrader 5 und konvertiert sie in ein optimiertes Format für die FinexTestingIDE.

**Workflow:** MQL5 Expert Advisor → JSON-Files → Python Converter → Parquet-Database → Testing IDE

---

## Phase 1: MQL5 Data Collector (2 Tage Setup)

### Ziel
Live-Tick-Daten von MetaTrader 5 in JSON-Format exportieren.

### Empfohlenes Symbol
**EURUSD** - liquideste Pair, 24h aktiv, niedrige Spreads, Standard-Benchmark

### Erwartete Datenmengen (2 Tage EURUSD)
- **Ticks:** ~1-3 Millionen
- **JSON-Größe:** 300-900 MB (roh)
- **Nach Kompression:** 30-90 MB

### 1.1 MQL5 Expert Advisor Code

### 1.2 Installation & Setup

#### Schritt 1: Code kompilieren
1. MetaTrader 5 öffnen
2. **F4** drücken (MetaEditor öffnen)
3. Neuen Expert Advisor erstellen: **File → New → Expert Advisor**
4. Obigen Code einfügen
5. **F7** drücken (kompilieren)
6. Auf Fehler prüfen

#### Schritt 2: Expert Advisor anwenden
1. **EURUSD** Chart öffnen (beliebiger Timeframe)
2. Expert Advisor auf Chart ziehen
3. **Input-Parameter prüfen:**
   - ExportPath: `C:\FinexData\`
   - MaxTicksPerFile: `50000`
   - CollectTicks: `true`
4. **OK** klicken
5. AutoTrading aktivieren (grüner Button)

#### Schritt 3: Monitoring
- **Expert-Tab** im Terminal prüfen
- Erfolgsmeldungen sollten erscheinen
- Export-Ordner checken auf neue JSON-Files

### 1.3 Quick-Checklist für 2-Tage-Run

**Vor dem Start:**
- [ ] Export-Ordner `C:\FinexData\` existiert
- [ ] Expert Advisor kompiliert ohne Fehler
- [ ] Auf EURUSD-Chart angehängt
- [ ] AutoTrading ist aktiv (grüner Button)
- [ ] Expert-Tab zeigt "TickCollector erfolgreich gestartet"

**Während dem Run:**
- [ ] Laptop läuft durchgehend (Energiesparmodus deaktivieren)
- [ ] MetaTrader bleibt geöffnet
- [ ] Internetverbindung stabil
- [ ] Gelegentlich Expert-Tab checken auf Fehler

**Nach 2 Tagen:**
- [ ] JSON-Files im Export-Ordner prüfen
- [ ] Dateigrößen validieren (sollten 300-900MB sein)
- [ ] Bereit für Phase 2!

---

## Phase 2: Python Daten-Konverter (1 Woche)

### Ziel
JSON-Files aus MQL5 in optimierte Parquet-Dateien konvertieren für schnellen Zugriff.

### 2.1 Requirements
```txt
pandas>=2.0.0
pyarrow>=12.0.0
numpy>=1.24.0
python-dateutil>=2.8.0
```

### 2.2 JSON → Parquet Converter

**Datei:** `tick_importer.py`

```python
"""
FinexTestingIDE Tick Data Importer
Konvertiert MQL5 JSON-Exports zu optimierten Parquet-Files
"""

import json
import pandas as pd
import numpy as np
import os
from pathlib import Path
from datetime import datetime, timezone
import pyarrow as pa
import pyarrow.parquet as pq
import logging
from typing import List, Dict, Optional

# Logging Setup
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class TickDataImporter:
    """Konvertiert MQL5 JSON-Exports zu Parquet-Format"""
    
    def __init__(self, source_dir: str, target_dir: str):
        self.source_dir = Path(source_dir)
        self.target_dir = Path(target_dir)
        self.target_dir.mkdir(parents=True, exist_ok=True)
        
        # Statistiken
        self.processed_files = 0
        self.total_ticks = 0
        self.errors = []
        
    def process_all_mql5_exports(self):
        """Verarbeitet alle JSON-Files aus MQL5-Export"""
        json_files = list(self.source_dir.glob("*_ticks.json"))
        
        if not json_files:
            logger.warning(f"Keine JSON-Files gefunden in {self.source_dir}")
            return
            
        logger.info(f"Gefunden: {len(json_files)} JSON-Files")
        
        for json_file in json_files:
            logger.info(f"Verarbeite: {json_file.name}")
            try:
                self.convert_json_to_parquet(json_file)
                self.processed_files += 1
            except Exception as e:
                error_msg = f"FEHLER bei {json_file.name}: {str(e)}"
                logger.error(error_msg)
                self.errors.append(error_msg)
                
        self._print_summary()
                
    def convert_json_to_parquet(self, json_file: Path):
        """Konvertiert einzelne JSON-Datei zu optimiertem Parquet"""
        
        # JSON laden und validieren
        with open(json_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            
        # Datenstruktur prüfen
        if 'ticks' not in data or 'metadata' not in data:
            raise ValueError("Ungültige JSON-Struktur - 'ticks' oder 'metadata' fehlt")
            
        ticks = data['ticks']
        metadata = data['metadata']
        
        if not ticks:
            logger.warning(f"Keine Ticks in {json_file.name}")
            return
            
        # DataFrame erstellen
        df = pd.DataFrame(ticks)
        
        # Datentyp-Optimierung
        df = self._optimize_datatypes(df)
        
        # Zeitstempel-Normalisierung
        df = self._normalize_timestamps(df)
        
        # Qualitäts-Checks
        df = self._quality_checks(df)
        
        # Sortierung
        df = df.sort_values('timestamp').reset_index(drop=True)
        
        # Duplikate entfernen (behalte letzten)
        initial_count = len(df)
        df = df.drop_duplicates(subset=['timestamp'], keep='last')
        if len(df) < initial_count:
            logger.info(f"Entfernt {initial_count - len(df)} Duplikate")
        
        # Parquet-Dateiname generieren
        symbol = metadata.get('symbol', 'UNKNOWN')
        start_time = pd.to_datetime(metadata.get('start_time', datetime.now()))
        parquet_name = f"{symbol}_{start_time.strftime('%Y%m%d_%H%M%S')}.parquet"
        parquet_path = self.target_dir / parquet_name
        
        # Metadaten für Parquet
        parquet_metadata = {
            'source_file': json_file.name,
            'symbol': symbol,
            'broker': metadata.get('broker', 'unknown'),
            'collector_version': metadata.get('collector_version', '1.0'),
            'processed_at': datetime.now().isoformat(),
            'tick_count': len(df)
        }
        
        # Als Parquet speichern mit Metadaten
        table = pa.Table.from_pandas(df)
        table = table.replace_schema_metadata(parquet_metadata)
        
        pq.write_table(table, parquet_path, compression='snappy')
        
        # Statistiken aktualisieren
        self.total_ticks += len(df)
        
        # Kompressionsrate berechnen
        json_size = json_file.stat().st_size
        parquet_size = parquet_path.stat().st_size
        compression_ratio = json_size / parquet_size if parquet_size > 0 else 0
        
        logger.info(f"✓ {parquet_name}: {len(df):,} Ticks, "
                   f"Kompression {compression_ratio:.1f}:1 "
                   f"({json_size/1024/1024:.1f}MB → {parquet_size/1024/1024:.1f}MB)")
        
    def _optimize_datatypes(self, df: pd.DataFrame) -> pd.DataFrame:
        """Optimiert Datentypen für bessere Performance und Speichernutzung"""
        
        # Numerische Optimierung
        for col in ['bid', 'ask', 'last', 'spread_pct']:
            if col in df.columns:
                df[col] = df[col].astype('float32')  # float64 → float32
                
        for col in ['tick_volume', 'real_volume', 'spread_points']:
            if col in df.columns:
                df[col] = df[col].astype('int32')    # int64 → int32
        
        # String-Kategorien für bessere Kompression
        if 'session' in df.columns:
            df['session'] = df['session'].astype('category')
            
        return df
        
    def _normalize_timestamps(self, df: pd.DataFrame) -> pd.DataFrame:
        """Normalisiert Zeitstempel zu UTC"""
        
        if 'timestamp' in df.columns:
            # Zu datetime konvertieren
            df['timestamp'] = pd.to_datetime(df['timestamp'])
            
            # Timezone-aware machen (annahme: UTC)
            if df['timestamp'].dt.tz is None:
                df['timestamp'] = df['timestamp'].dt.tz_localize('UTC')
                
        return df
        
    def _quality_checks(self, df: pd.DataFrame) -> pd.DataFrame:
        """Führt Datenqualitäts-Checks durch"""
        
        initial_count = len(df)
        
        # Entferne Rows mit invaliden Bid/Ask-Werten
        df = df[(df['bid'] > 0) & (df['ask'] > 0) & (df['ask'] >= df['bid'])]
        
        # Entferne extreme Outliers (Spreads > 10%)
        if 'spread_pct' in df.columns:
            df = df[df['spread_pct'] <= 10.0]
            
        removed = initial_count - len(df)
        if removed > 0:
            logger.warning(f"Qualitäts-Check: {removed} invalide Ticks entfernt")
            
        return df
        
    def _print_summary(self):
        """Druckt Zusammenfassung der Verarbeitung"""
        logger.info("=" * 50)
        logger.info("VERARBEITUNGS-ZUSAMMENFASSUNG")
        logger.info("=" * 50)
        logger.info(f"Verarbeitete Dateien: {self.processed_files}")
        logger.info(f"Gesamte Ticks: {self.total_ticks:,}")
        logger.info(f"Fehler: {len(self.errors)}")
        
        if self.errors:
            logger.error("FEHLER-LISTE:")
            for error in self.errors:
                logger.error(f"  - {error}")

# Hauptfunktion
if __name__ == "__main__":
    # Konfiguration
    SOURCE_DIR = "C:/FinexData/"          # MQL5 Export-Ordner
    TARGET_DIR = "./data/processed/"       # Parquet-Ziel
    
    logger.info("FinexTestingIDE Tick Data Importer gestartet")
    
    # Importer initialisieren und ausführen
    importer = TickDataImporter(SOURCE_DIR, TARGET_DIR)
    importer.process_all_mql5_exports()
    
    logger.info("Import abgeschlossen!")
```

---

## Phase 3: Daten-Loader für Testing-IDE (1 Woche)

### Ziel
Einfacher, schneller Zugriff auf Parquet-Daten für Backtesting.

### 3.1 Parquet Data Loader

**Datei:** `data_loader.py`

```python
"""
FinexTestingIDE Data Loader
Schneller Zugriff auf Parquet-basierte Tick-Daten
"""

import pandas as pd
import pyarrow.parquet as pq
from pathlib import Path
from typing import List, Dict, Optional, Tuple
from datetime import datetime, timedelta
import logging

logger = logging.getLogger(__name__)

class TickDataLoader:
    """Lädt und verwaltet Tick-Daten für Backtesting"""
    
    def __init__(self, data_dir: str = "./data/processed/"):
        self.data_dir = Path(data_dir)
        if not self.data_dir.exists():
            raise FileNotFoundError(f"Daten-Verzeichnis nicht gefunden: {data_dir}")
            
        self._symbol_cache = {}  # Cache für geladene Daten
        
    def list_available_symbols(self) -> List[str]:
        """Listet alle verfügbaren Symbole auf"""
        parquet_files = list(self.data_dir.glob("*.parquet"))
        symbols = set()
        
        for file in parquet_files:
            try:
                # Symbol aus Dateiname extrahieren (FORMAT: SYMBOL_YYYYMMDD_HHMMSS.parquet)
                symbol = file.name.split('_')[0]
                symbols.add(symbol)
            except IndexError:
                logger.warning(f"Dateiname hat unerwartetes Format: {file.name}")
                
        return sorted(list(symbols))
    
    def get_symbol_info(self, symbol: str) -> Dict[str, any]:
        """Gibt detaillierte Informationen über ein Symbol zurück"""
        files = self._get_symbol_files(symbol)
        
        if not files:
            return {'error': f'Keine Daten für Symbol {symbol} gefunden'}
            
        try:
            # Alle Files einlesen für Statistiken
            dataframes = []
            total_size = 0
            
            for file in files:
                df = pd.read_parquet(file)
                dataframes.append(df)
                total_size += file.stat().st_size
                
            # Kombinieren
            combined_df = pd.concat(dataframes, ignore_index=True)
            combined_df = combined_df.sort_values('timestamp')
            
            return {
                'symbol': symbol,
                'files': len(files),
                'total_ticks': len(combined_df),
                'date_range': {
                    'start': combined_df['timestamp'].min().isoformat(),
                    'end': combined_df['timestamp'].max().isoformat(),
                    'days': (combined_df['timestamp'].max() - combined_df['timestamp'].min()).days
                },
                'statistics': {
                    'avg_spread_points': combined_df['spread_points'].mean() if 'spread_points' in combined_df else None,
                    'avg_spread_pct': combined_df['spread_pct'].mean() if 'spread_pct' in combined_df else None,
                    'tick_frequency_per_second': len(combined_df) / ((combined_df['timestamp'].max() - combined_df['timestamp'].min()).total_seconds())
                },
                'file_size_mb': total_size / 1024 / 1024,
                'sessions': combined_df['session'].value_counts().to_dict() if 'session' in combined_df else {}
            }
            
        except Exception as e:
            return {'error': f'Fehler beim Laden von {symbol}: {str(e)}'}
    
    def load_symbol_data(self, 
                        symbol: str,
                        start_date: Optional[str] = None,
                        end_date: Optional[str] = None,
                        use_cache: bool = True) -> pd.DataFrame:
        """
        Lädt Tick-Daten für ein Symbol
        
        Args:
            symbol: Währungspaar (z.B. 'EURUSD')
            start_date: Start-Datum (ISO format oder None für alle Daten)
            end_date: End-Datum (ISO format oder None für alle Daten) 
            use_cache: Ob gecachte Daten verwendet werden sollen
            
        Returns:
            DataFrame mit Tick-Daten
        """
        
        cache_key = f"{symbol}_{start_date}_{end_date}"
        
        # Cache check
        if use_cache and cache_key in self._symbol_cache:
            logger.info(f"Verwende gecachte Daten für {symbol}")
            return self._symbol_cache[cache_key].copy()
        
        # Files für Symbol finden
        files = self._get_symbol_files(symbol)
        
        if not files:
            raise ValueError(f"Keine Daten für Symbol {symbol} gefunden")
        
        logger.info(f"Lade {len(files)} Dateien für {symbol}")
        
        # Alle Files einlesen und kombinieren
        dataframes = []
        for file in files:
            try:
                df = pd.read_parquet(file)
                dataframes.append(df)
            except Exception as e:
                logger.warning(f"Fehler beim Lesen von {file}: {e}")
                
        if not dataframes:
            raise ValueError(f"Keine gültigen Daten für {symbol} gefunden")
            
        # Kombinieren und sortieren
        combined_df = pd.concat(dataframes, ignore_index=True)
        combined_df = combined_df.sort_values('timestamp').reset_index(drop=True)
        
        # Duplikate entfernen (neueste behalten)
        combined_df = combined_df.drop_duplicates(subset=['timestamp'], keep='last')
        
        # Datums-Filter anwenden
        if start_date:
            combined_df = combined_df[combined_df['timestamp'] >= pd.to_datetime(start_date)]
        if end_date:
            combined_df = combined_df[combined_df['timestamp'] <= pd.to_datetime(end_date)]
            
        # Cache update
        if use_cache:
            self._symbol_cache[cache_key] = combined_df.copy()
            
        logger.info(f"✓ Geladen: {len(combined_df):,} Ticks für {symbol}")
        return combined_df
    
    def get_data_summary(self) -> Dict[str, Dict]:
        """Erstellt Übersicht über alle verfügbaren Daten"""
        symbols = self.list_available_symbols()
        summary = {}
        
        logger.info(f"Erstelle Übersicht für {len(symbols)} Symbole...")
        
        for symbol in symbols:
            summary[symbol] = self.get_symbol_info(symbol)
            
        return summary
    
    def _get_symbol_files(self, symbol: str) -> List[Path]:
        """Findet alle Parquet-Files für ein Symbol"""
        pattern = f"{symbol}_*.parquet"
        files = list(self.data_dir.glob(pattern))
        return sorted(files)  # Chronologische Sortierung
    
    def clear_cache(self):
        """Leert den Daten-Cache"""
        self._symbol_cache.clear()
        logger.info("Daten-Cache geleert")

# Test- und Demo-Funktion
def main():
    """Demo-Funktion für Data Loader"""
    logger.info("=== FinexTestingIDE Data Loader Demo ===")
    
    try:
        loader = TickDataLoader()
        
        # Verfügbare Symbole auflisten
        symbols = loader.list_available_symbols()
        logger.info(f"Verfügbare Symbole: {symbols}")
        
        if not symbols:
            logger.error("Keine Daten gefunden! Bitte erst MQL5-Daten sammeln und konvertieren.")
            return
            
        # Detaillierte Übersicht
        summary = loader.get_data_summary()
        
        print("\n" + "="*80)
        print("DATEN-ÜBERSICHT")
        print("="*80)
        
        for symbol, info in summary.items():
            if 'error' not in info:
                print(f"\n{symbol}:")
                print(f"  📊 {info['total_ticks']:,}

