# Duplicate Detection & Data Mode System

## 📋 Overview

Das System schützt die Datenintegrität durch:
1. **Artificial Duplicate Detection** - Erkennt manuell kopierte Parquet-Files
2. **Cross-Directory Detection** - Erkennt Duplikate über data_collector Verzeichnisse hinweg (NEW in C#003)
3. **Data Mode Support** - Steuert das Handling von natürlichen Duplikaten

---

## 🗂️ Directory Structure (NEW in C#003)

### Hierarchische Organisation

```
data/processed/
├── .parquet_index.json          # Zentraler Index
├── mt5/                          # data_collector: MetaTrader 5
│   ├── EURUSD/                   # Symbol-spezifische Verzeichnisse
│   │   ├── EURUSD_20250923_120000.parquet
│   │   └── EURUSD_20250923_130000.parquet
│   ├── GBPUSD/
│   └── USDJPY/
└── ib/                           # data_collector: Interactive Brokers (zukünftig)
    └── EURUSD/
        └── EURUSD_20250923_120000.parquet
```

### data_collector Feld

**In JSON Metadata (TickCollector v1.0.4):**
```json
{
  "metadata": {
    "symbol": "EURUSD",
    "data_collector": "mt5",      // NEU in v1.0.4
    "data_format_version": "1.0.4",
    "broker": "Vantage...",
    ...
  }
}
```

**In Parquet Metadata:**
```python
parquet_metadata = {
    "source_file": "EURUSD_20250923_120000_ticks.json",
    "symbol": "EURUSD",
    "data_collector": "mt5",      // Wird aus JSON übernommen
    "broker": "Vantage...",
    ...
}
```

**Fallback:** Wenn `data_collector` fehlt → automatisch "mt5"

---

## 📄 Import Behavior

### Normaler Import (C#003)
```bash
python tick_importer.py

Processing: EURUSD_20250923_120000_ticks.json
  → Extracting data_collector: 'mt5'
  → Target: data/processed/mt5/EURUSD/
  → Checking for existing duplicates (all collectors)...
  → No duplicates found ✅
  → Creating: EURUSD_20250923_120000.parquet
  ✓ mt5/EURUSD/EURUSD_20250923_120000.parquet: 45,231 Ticks
    Compression 10.2:1 (45.3MB → 4.4MB)
```

### Re-Import Detection - Same Collector
```bash
python tick_importer.py

Processing: EURUSD_20250923_120000_ticks.json
  → Extracting data_collector: 'mt5'
  → Target: data/processed/mt5/EURUSD/
  → Checking for existing duplicates (all collectors)...
  ⚠️  Found existing Parquet: mt5/EURUSD/EURUSD_20250923_120000.parquet
      Existing: data_collector='mt5' | Importing: data_collector='mt5'
  
ERROR: 
================================================================================
⚠️  ARTIFICIAL DUPLICATE DETECTED - DATA INTEGRITY VIOLATION
================================================================================

📄 Original Source JSON:
   EURUSD_20250923_120000_ticks.json

📦 Duplicate Parquet Files Found: 1

   [1] mt5/EURUSD/EURUSD_20250923_120000.parquet
       Ticks:          45,231
       Range:     2025-09-23 12:00:00 → 2025-09-23 14:30:45
       Size:            4.42 MB

📋 Parquet Metadata Comparison:

   • source_file        ✅ IDENTICAL
   • symbol             ✅ IDENTICAL
   • data_collector     ✅ IDENTICAL
       [1] mt5
   • broker             ✅ IDENTICAL
   • collector_version  ✅ IDENTICAL
   • tick_count         ✅ IDENTICAL
   • processed_at       ⚠️  DIFFERENT
       [1] 2025-09-23T12:05:30
       [2] 2025-09-23T14:32:15

🔬 Data Similarity Analysis:
   • Tick Counts:  ✅ IDENTICAL
   • Time Ranges:  ✅ IDENTICAL

⚠️  🔴 CRITICAL - Complete data duplication detected
   Impact: Identical files, test results will be severely compromised

💡 Recommended Actions:
   1. DELETE the older file (check processed_at timestamp)
   2. Keep the file with most recent processed_at
   3. Rebuild index: python python/cli/data_index_cli.py rebuild

================================================================================

→ Überspringe Import (Duplikat existiert bereits)
```

### Cross-Collector Duplicate Detection (NEW in C#003) 🔥

```bash
# Scenario: Dieselbe Quelle versehentlich unter anderem Collector importiert

python tick_importer.py

Processing: EURUSD_20250923_120000_ticks.json
  → Extracting data_collector: 'ib'  # Andere Quelle!
  → Target: data/processed/ib/EURUSD/
  → Checking for existing duplicates (all collectors)...
  ⚠️  Found existing Parquet: mt5/EURUSD/EURUSD_20250923_120000.parquet
      Existing: data_collector='mt5' | Importing: data_collector='ib'
      ⚠️  CROSS-COLLECTOR DUPLICATE DETECTED!

ERROR:
================================================================================
⚠️  ARTIFICIAL DUPLICATE DETECTED - DATA INTEGRITY VIOLATION
================================================================================

📄 Original Source JSON:
   EURUSD_20250923_120000_ticks.json

📦 Duplicate Parquet Files Found: 1

   [1] mt5/EURUSD/EURUSD_20250923_120000.parquet
       Ticks:          45,231
       Range:     2025-09-23 12:00:00 → 2025-09-23 14:30:45
       Size:            4.42 MB

📋 Parquet Metadata Comparison:

   • source_file        ✅ IDENTICAL
   • symbol             ✅ IDENTICAL
   • data_collector     ⚠️  CROSS-COLLECTOR DUPLICATE!
       [1] mt5
       [2] ib
   • broker             ✅ IDENTICAL
   • collector_version  ✅ IDENTICAL

⚠️  🔴 CRITICAL - Cross-Collector Duplication
   Impact: Same data imported under different collectors: mt5, ib

💡 Recommended Actions:
   1. INVESTIGATE why the same source was imported under different collectors
   2. DELETE one of the duplicate files (choose the wrong collector)
   3. Check your import workflow to prevent cross-collector duplicates
   4. Rebuild index: python python/cli/data_index_cli.py rebuild

================================================================================
```

---

## 🚨 Artificial Duplicate Detection (Cross-Directory Protection)

### Enhanced Two-Layer Protection (C#003)

#### 🛡️ Layer 1: Import Prevention (tick_importer.py)
**Sucht über ALLE data_collector Verzeichnisse hinweg**

```python
# VORHER (Old):
existing_files = list(self.target_dir.glob(f"{symbol}_*.parquet"))
# Sucht nur: data/processed/EURUSD_*.parquet

# NACHHER (C#003):
search_pattern = f"*/{symbol}/{symbol}_*.parquet"
existing_files = list(self.target_dir.glob(search_pattern))
# Sucht: data/processed/*/EURUSD/EURUSD_*.parquet
#   → mt5/EURUSD/*.parquet
#   → ib/EURUSD/*.parquet
#   → (alle collector)
```

**Was es erkennt:**
```
Scenario 1: Same Collector Re-Import
────────────────────────────────────────
mt5/EURUSD/EURUSD_20250923_120000.parquet    (exists)
mt5/EURUSD/EURUSD_20250923_120000.parquet    (trying to import)
→ 🔴 DUPLICATE DETECTED (same collector)

Scenario 2: Cross-Collector Duplicate
────────────────────────────────────────
mt5/EURUSD/EURUSD_20250923_120000.parquet    (exists)
ib/EURUSD/EURUSD_20250923_120000.parquet     (trying to import)
→ 🔴 CROSS-COLLECTOR DUPLICATE!
```

#### 🛡️ Layer 2: Load Validation (TickDataLoader)
**Validiert beim Laden über alle Collector hinweg**

```python
# Uses index which scans recursively: glob("**/*.parquet")
# Lädt ALLE Parquet-Files für das Symbol, egal unter welchem collector

files = self.index_manager.get_relevant_files(symbol, start, end)
# Returns: [
#   Path("data/processed/mt5/EURUSD/file1.parquet"),
#   Path("data/processed/ib/EURUSD/file2.parquet"),  # if exists
# ]

# Check for duplicates across ALL files
duplicate_report = self._check_artificial_duplicates(files)
```

### Metadata-Vergleich (Enhanced in C#003)

Der Duplicate Report vergleicht jetzt auch `data_collector`:

```
📋 Parquet Metadata Comparison:

   • source_file        ✅ IDENTICAL
   • symbol             ✅ IDENTICAL
   • data_collector     ⚠️  CROSS-COLLECTOR DUPLICATE!  // NEU!
       [1] mt5
       [2] ib
   • broker             ✅ IDENTICAL
```

**Wichtig:** `data_collector` wird **NICHT** als Duplicate-Kriterium verwendet!
- Duplikat = Gleiche `source_file`
- `data_collector` wird nur zur **Info** angezeigt

---

## 🎯 Data Modes

### `data_mode="raw"`
- **Zweck:** Maximaler Realismus für Stress-Tests
- **Verhalten:** Alle Duplikate bleiben erhalten (wie vom Broker empfangen)
- **Use-Case:** Phase 3 Testing, Algo-Stress unter realen Bedingungen

### `data_mode="realistic"`
- **Zweck:** Normale Test-Bedingungen
- **Verhalten:** Natürliche Duplikate werden entfernt
- **Use-Case:** Standard-Testing, Performance-Validierung

### `data_mode="clean"`
- **Zweck:** Optimierte Test-Bedingungen  
- **Verhalten:** Natürliche Duplikate werden entfernt (wie realistic)
- **Use-Case:** Benchmark-Tests, Clean-Data-Szenarien

---

## 💻 Usage in Code

### In Scenario Config (JSON)

```json
{
  "global": {
    "data_mode": "realistic"
  },
  "scenarios": [
    {
      "name": "EURUSD_stress_test",
      "symbol": "EURUSD",
      "data_mode": "raw"  // Override für dieses Scenario
    }
  ]
}
```

### Direct API Usage

```python
from python.data_worker.data_loader.core import TickDataLoader
from python.data_worker.data_loader.exceptions import ArtificialDuplicateException

loader = TickDataLoader('./data/processed/')

try:
    df = loader.load_symbol_data(
        symbol="EURUSD",
        data_mode="realistic",
        detect_artificial_duplicates=True  # Default
    )
except ArtificialDuplicateException as e:
    print(e.report.get_detailed_report())
    # Cross-Collector duplicate detected!
```
