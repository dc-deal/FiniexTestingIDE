# Duplicate Detection & Data Mode System

## 📋 Overview

Das neue System schützt die Datenintegrität durch:
1. **Artificial Duplicate Detection** - Erkennt manuell kopierte Parquet-Files
2. **Data Mode Support** - Steuert das Handling von natürlichen Duplikaten

---

## 🔄 Import Behavior

### Normaler Import
```bash
python tick_importer.py

Processing: AUDUSD_20250919_053807_ticks.json
  → Checking for existing duplicates...
  → No duplicates found ✅
  → Creating: AUDUSD_20250919_053807.parquet
  ✓ 12,847 Ticks, Compression 10.2:1 (45.3MB → 4.4MB)
```

### Re-Import Detection (Layer 1)
```bash
python tick_importer.py

Processing: AUDUSD_20250919_053807_ticks.json
  → Checking for existing duplicates...
  ⚠️  Found existing Parquet from same source: AUDUSD_20250919_053807.parquet
  
ERROR: 
================================================================================
⚠️  ARTIFICIAL DUPLICATE DETECTED - DATA INTEGRITY VIOLATION
================================================================================

📄 Original Source JSON:
   AUDUSD_20250919_053807_ticks.json

📦 Duplicate Parquet Files Found: 2

   [1] AUDUSD_20250919_053807.parquet
       Ticks:          12,847
       Range:     2025-09-19 05:38:07 → 2025-09-19 08:15:23
       Size:            1.24 MB

   [2] (new import)
       Ticks:          12,847
       Range:     2025-09-19 05:38:07 → 2025-09-19 08:15:23
       Size:            0.00 MB

🔬 Similarity Analysis:
   • Tick Counts:  ✅ IDENTICAL
   • Time Ranges:  ✅ IDENTICAL

⚠️  🔴 CRITICAL - Complete data duplication detected
   Impact: Test results will be severely compromised (2x tick density)

💡 Recommended Actions:
   1. DELETE one of the duplicate Parquet files manually
   2. Keep only the most recent file (check file modification date)
   3. Re-run the test after cleanup
   4. Prevent: Avoid manual copying of Parquet files

================================================================================

→ Überspringe Import (Duplikat existiert bereits)

VERARBEITUNGS-ZUSAMMENFASSUNG
Verarbeitete Dateien: 0
Fehler: 1
  - DUPLICATE DETECTED bei AUDUSD_20250919_053807_ticks.json
```

### Manual File Duplication Detection (Layer 2)
```bash
# User copies file manually
cp data/processed/AUDUSD_20250919_053807.parquet \
   data/processed/AUDUSD_20250919_053807_COPY.parquet

# Run test
python run_strategy.py --scenario audusd_test

ERROR: [Same detailed report as above, but detected during load]
```

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

## 🚨 Artificial Duplicate Detection (Two-Layer Protection)

Das System hat **zwei Verteidigungslinien** gegen künstliche Duplikate:

### 🛡️ Layer 1: Import Prevention (tick_importer.py)
**Verhindert versehentliche Re-Imports beim Parquet-Erstellen**

Prüft BEVOR ein neues Parquet geschrieben wird, ob bereits ein File existiert mit derselben `source_file`:

```
Scenario: Re-Import derselben JSON
─────────────────────────────────────
1. Import: AUDUSD_20250919_053807_ticks.json
   → Erstellt: AUDUSD_20250919_053807.parquet ✅

2. Re-Import: AUDUSD_20250919_053807_ticks.json (nochmal)
   → Check: Parquet mit source_file bereits vorhanden
   → ABORT: ArtificialDuplicateException ❌
   → Überspringt Import, zeigt Report
```

### 🛡️ Layer 2: Load Validation (TickDataLoader)
**Erkennt manuelle File-Duplikationen beim Laden**

Prüft beim Laden ALLER Parquet-Files eines Symbols, ob mehrere dieselbe `source_file` haben:

```
Scenario: Manuelle File-Kopie
─────────────────────────────────────
1. User kopiert manuell:
   cp AUDUSD_20250919_053807.parquet \
      AUDUSD_20250919_053807_COPY.parquet

2. Test startet → load_symbol_data("AUDUSD")
   → Findet beide Files
   → Check: BEIDE haben source_file = "AUDUSD_20250919_053807_ticks.json"
   → ABORT: ArtificialDuplicateException ❌
   → Zeigt detaillierten Report
```

### Was wird erkannt?

Wenn **zwei oder mehr Parquet-Files dieselbe `source_file` Metadaten haben**:

```
AUDUSD_20250919_053807.parquet      (source: AUDUSD_20250919_053807_ticks.json)
AUDUSD_20250919_053807_COPY.parquet (source: AUDUSD_20250919_053807_ticks.json)
                                     ↑
                            BEIDE haben gleiche source_file!
```

### Beispiel-Report

```
================================================================================
⚠️  ARTIFICIAL DUPLICATE DETECTED - DATA INTEGRITY VIOLATION
================================================================================

📄 Original Source JSON:
   AUDUSD_20250919_053807_ticks.json

📦 Duplicate Parquet Files Found: 2

   [1] AUDUSD_20250919_053807.parquet
       Ticks:          12,847
       Range:     2025-09-19 05:38:07 → 2025-09-19 08:15:23
       Size:            1.24 MB

   [2] AUDUSD_20250919_053807_COPY.parquet
       Ticks:          12,847
       Range:     2025-09-19 05:38:07 → 2025-09-19 08:15:23
       Size:            1.24 MB

🔬 Similarity Analysis:
   • Tick Counts:  ✅ IDENTICAL
   • Time Ranges:  ✅ IDENTICAL

⚠️  🔴 CRITICAL - Complete data duplication detected
   Impact: Test results will be severely compromised (2x tick density)

💡 Recommended Actions:
   1. DELETE one of the duplicate Parquet files manually
   2. Keep only the most recent file (check file modification date)
   3. Re-run the test after cleanup
   4. Prevent: Avoid manual copying of Parquet files

================================================================================
```

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
    # Load with raw mode (keeps all duplicates)
    df = loader.load_symbol_data(
        symbol="EURUSD",
        data_mode="raw",
        detect_artificial_duplicates=True  # Default
    )
except ArtificialDuplicateException as e:
    print(e.report.get_detailed_report())
    # Handle error: cleanup, alert, abort
```

### In TickDataPreparator

```python
preparator = TickDataPreparator(data_worker)

warmup, test = preparator.prepare_test_and_warmup_split(
    symbol="EURUSD",
    warmup_bars_needed=105,
    test_ticks_count=773,
    data_mode="raw"  # From scenario config
)
```

---

## 🔧 Integration Points

### 1. Config Loader → Preparator
```python
# In batch_orchestrator.py
scenario_contract = self._calculate_scenario_requirements(workers)
preparator = TickDataPreparator(self.data_worker)

warmup_ticks, test_iterator = preparator.prepare_test_and_warmup_split(
    symbol=scenario.symbol,
    warmup_bars_needed=scenario_contract["max_warmup_bars"],
    test_ticks_count=scenario.max_ticks or 1000,
    data_mode=scenario.data_mode,  # ← From scenario config
    start_date=scenario.start_date,
    end_date=scenario.end_date,
)
```

### 2. Error Handling
```python
try:
    df = loader.load_symbol_data(...)
except ArtificialDuplicateException as e:
    vLog.error(e.report.get_detailed_report())
    # Stop test execution
    raise
except InvalidDataModeException as e:
    vLog.error(f"Invalid data_mode: {e}")
    # Use fallback mode
    df = loader.load_symbol_data(..., data_mode="realistic")
```

---

## 📊 Logging Output

### Normal Load (no duplicates)
```
Loading 3 files for EURUSD
No natural duplicates found in 7,689 ticks
✓ Loaded: 7,689 ticks for EURUSD
```

### With Natural Duplicates (realistic mode)
```
Loading 3 files for EURUSD
Removed 127 natural duplicates from 7,816 total ticks (1.63% of data) [data_mode=realistic]
✓ Loaded: 7,689 ticks for EURUSD
```

### With Natural Duplicates (raw mode)
```
Loading 3 files for EURUSD
Keeping all ticks including natural duplicates [data_mode=raw]
✓ Loaded: 7,816 ticks for EURUSD
```

### Artificial Duplicate Detected
```
ERROR: 

================================================================================
⚠️  ARTIFICIAL DUPLICATE DETECTED - DATA INTEGRITY VIOLATION
================================================================================
[... full report ...]
```

---

## 🛡️ Best Practices

### DO ✅
- Use `data_mode="raw"` for stress tests in Phase 3
- Use `data_mode="realistic"` for standard testing
- Let both protection layers work automatically (Importer + Loader)
- Review import logs for duplicate warnings
- Delete duplicate files manually if detected

### DON'T ❌
- Never manually copy Parquet files in `processed/`
- Don't disable `detect_artificial_duplicates` in production
- Don't ignore `ArtificialDuplicateException` warnings
- Don't re-import the same JSON without deleting old Parquet first

### Why Two Layers?

**Import Layer (tick_importer.py):**
- Prevents accidental re-imports
- Zero overhead (only runs during import)
- Catches 95% of duplicate scenarios

**Load Layer (TickDataLoader):**
- Safety net for manual file operations
- Minimal overhead (cached per symbol, runs once)
- Catches edge cases and manual mistakes

**Result:** Maximum safety with acceptable performance impact

---

## 🧪 Testing

### Test Layer 1: Import Prevention
```bash
# First import (should succeed)
python tick_importer.py

# Second import of same JSON (should detect duplicate)
python tick_importer.py
# Expected: ArtificialDuplicateException, skips import
```

### Test Layer 2: Load Validation
```bash
# Create artificial duplicate for testing
cp data/processed/EURUSD_20250919_053807.parquet \
   data/processed/EURUSD_20250919_053807_TEST.parquet

# Run test - should throw ArtificialDuplicateException during load
python run_strategy.py --scenario eurusd_test
# Expected: Exception with detailed report
```

### Verify Data Mode Behavior
```python
# Load same symbol with different modes
df_raw = loader.load_symbol_data("EURUSD", data_mode="raw")
df_realistic = loader.load_symbol_data("EURUSD", data_mode="realistic")

print(f"Raw mode ticks: {len(df_raw)}")
print(f"Realistic mode ticks: {len(df_realistic)}")
print(f"Duplicates removed: {len(df_raw) - len(df_realistic)}")
```

### Test Both Layers Together
```bash
# Workflow simulation
cd data/raw

# 1. Import data (Layer 1 active)
python ../../tick_importer.py
# Expected: Success, creates Parquet

# 2. Try re-import (Layer 1 should catch)
python ../../tick_importer.py
# Expected: Duplicate detected, skips import

# 3. Manual copy (bypasses Layer 1)
cd ../processed
cp EURUSD_20250919_053807.parquet EURUSD_MANUAL_COPY.parquet

# 4. Run test (Layer 2 should catch)
cd ../..
python run_strategy.py --scenario eurusd_test
# Expected: Duplicate detected during load
```

---

## 📝 Migration Notes

### Old Code (drop_duplicates parameter)
```python
df = loader.load_symbol_data(
    symbol="EURUSD",
    drop_duplicates=True  # OLD
)
```

### New Code (data_mode parameter)
```python
df = loader.load_symbol_data(
    symbol="EURUSD",
    data_mode="realistic"  # NEW
)
```

**Mapping:**
- `drop_duplicates=True` → `data_mode="realistic"`
- `drop_duplicates=False` → `data_mode="raw"`
