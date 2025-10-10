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

---

## 🔧 Migration Guide (Old → C#003)

### Step 1: Update Code Files
Replace these files with C#003 versions:
- `tick_importer.py` (V1.1 → V1.2)
- `parquet_index.py` (add recursive scanning)
- `core.py` (add hierarchical pattern)
- `exceptions.py` (V1.1 → V1.2 with data_collector)
- `TickCollector.mq5` (V1.0.3 → V1.0.4)

### Step 2: Migrate Existing Data

**Option A: Manual Move** (empfohlen)
```bash
cd data/processed/

# Create collector structure
mkdir -p mt5/{EURUSD,GBPUSD,USDJPY,AUDUSD}

# Move existing files
mv EURUSD_*.parquet mt5/EURUSD/
mv GBPUSD_*.parquet mt5/GBPUSD/
mv USDJPY_*.parquet mt5/USDJPY/
mv AUDUSD_*.parquet mt5/AUDUSD/

# Rebuild index
python python/cli/data_index_cli.py rebuild
```

**Option B: Fresh Import**
```bash
# Delete old structure
rm -rf data/processed/*.parquet
rm data/processed/.parquet_index.json

# Re-import with new code
python python/data_worker/tick_importer.py
```

### Step 3: Update MQL5 Collector

1. Open `mql5/TickCollector.mq5` in MetaEditor
2. Find: `"data_format_version": "1.0.3"`
3. Replace with: `"data_format_version": "1.0.4"`
4. Add below: `"data_collector": "mt5"`
5. Compile (F7) and restart EA

### Step 4: Verify Migration

```bash
# Check structure
tree data/processed/ -L 3

# Expected output:
# data/processed/
# ├── .parquet_index.json
# └── mt5/
#     ├── EURUSD/
#     │   ├── EURUSD_20250923_120000.parquet
#     │   └── ...
#     ├── GBPUSD/
#     └── USDJPY/

# Test loading
python -c "
from python.data_worker.data_loader.core import TickDataLoader
loader = TickDataLoader()
print(loader.list_available_symbols())
df = loader.load_symbol_data('EURUSD')
print(f'Loaded {len(df)} ticks')
"
```

---

## 🧪 Testing Cross-Directory Detection

### Test 1: Same Collector Re-Import
```bash
# Import once
python python/data_worker/tick_importer.py
# ✅ Creates: mt5/EURUSD/EURUSD_*.parquet

# Try re-import
python python/data_worker/tick_importer.py
# ❌ Should detect: DUPLICATE DETECTED
```

### Test 2: Cross-Collector Duplicate (Simulated)
```bash
# Copy to simulate different collector
mkdir -p data/processed/ib/EURUSD
cp data/processed/mt5/EURUSD/EURUSD_20250923_120000.parquet \
   data/processed/ib/EURUSD/

# Manually edit Parquet metadata to set data_collector='ib'
# (In real scenario, this would happen during import)

# Try loading - should detect cross-collector duplicate
python -c "
from python.data_worker.data_loader.core import TickDataLoader
loader = TickDataLoader()
df = loader.load_symbol_data('EURUSD')
"
# ❌ Should throw: CROSS-COLLECTOR DUPLICATE!
```

### Test 3: Manual File Copy
```bash
# Copy file within same collector
cp data/processed/mt5/EURUSD/EURUSD_20250923_120000.parquet \
   data/processed/mt5/EURUSD/EURUSD_20250923_120000_COPY.parquet

# Try loading
python run_strategy.py --scenario eurusd_test
# ❌ Should detect: DUPLICATE (same source_file)
```

---

## 📊 Logging Output (C#003)

### Successful Import
```
✓ mt5/EURUSD/EURUSD_20250923_120000.parquet: 45,231 Ticks
  Compression 10.2:1 (45.3MB → 4.4MB)
```

### Cross-Collector Warning
```
⚠️  Found existing Parquet: mt5/EURUSD/EURUSD_20250923_120000.parquet
    Existing: data_collector='mt5' | Importing: data_collector='ib'
    
🔴 CRITICAL - Cross-Collector Duplication
   Impact: Same data imported under different collectors: mt5, ib
```

### Index Rebuild
```
🔄 Rebuilding Parquet index...
🔍 Scanning Parquet files for index... (recursive)
✅ Index built: 47 files across 4 symbols in 0.23s
```

---

## 🛡️ Best Practices (Updated for C#003)

### DO ✅
- Use hierarchical structure: `{collector}/{symbol}/`
- Set `data_collector` in JSON metadata (v1.0.4+)
- Let both protection layers work automatically
- Use `data_mode="realistic"` for standard testing
- Review import logs for cross-collector warnings
- Rebuild index after manual file operations

### DON'T ❌
- Never manually copy Parquet files between collectors
- Don't import same source under different collectors
- Don't bypass the directory structure
- Don't disable `detect_artificial_duplicates`
- Don't ignore cross-collector duplicate warnings

### Why Cross-Directory Detection?

**Problem:** User könnte versehentlich dieselben Daten mehrfach importieren:
```
data/processed/
├── mt5/EURUSD/file.parquet       (from source.json)
└── ib/EURUSD/file.parquet        (from same source.json!)
```

**Solution:** Duplicate-Checker durchsucht **ALLE** Collector-Verzeichnisse:
- Erkennt Cross-Collector Duplikate
- Zeigt data_collector Unterschiede an
- Verhindert gemischte Datenquellen im Test

---

## 🔍 Troubleshooting

### "Symbol not found in index"
```bash
# Rebuild index with recursive scanning
python python/cli/data_index_cli.py rebuild
```

### "No files found for symbol"
```bash
# Check directory structure
tree data/processed/ -L 3

# Ensure files are in: {collector}/{symbol}/ format
ls data/processed/mt5/EURUSD/
```

### Cross-Collector Duplicate persists
```bash
# Find all occurrences
find data/processed -name "*EURUSD_20250923*"

# Delete unwanted collector version
rm -rf data/processed/ib/EURUSD/EURUSD_20250923_120000.parquet

# Rebuild index
python python/cli/data_index_cli.py rebuild
```

---

## 📝 Summary of Changes (C#003)

| Feature | Old (V1.1) | New (C#003) |
|---------|-----------|-------------|
| **Structure** | Flat: `SYMBOL_*.parquet` | Hierarchical: `{collector}/{symbol}/` |
| **Duplicate Search** | Single directory | Cross-directory (all collectors) |
| **Metadata** | Basic fields | + `data_collector` field |
| **Index Scan** | `glob("*.parquet")` | `glob("**/*.parquet")` (recursive) |
| **Error Report** | Basic comparison | + Cross-collector detection |
| **MQL5 Version** | v1.0.3 | v1.0.4 (+ data_collector) |

**Migration Required:** ✅ Yes  
**Breaking Changes:** ✅ Yes (directory structure)  
**Data Loss:** ❌ No (manual migration possible)  
**Backward Compatible:** ⚠️ Partial (fallback to "mt5")