# Duplicate Detection

## 📋 Overview

The system protects data integrity through:
1. **Artificial Duplicate Detection** - Detects a source file imported a second time
2. **Cross-Broker Detection** - Detects the same source imported under a different broker directory

**Natural duplicates are kept as received.** Two ticks with the same time and the same prices are
what the venue sent, and nothing in the import or in a run's data loading filters them. There is no
setting that changes this: the `data_mode` key that once selected a filter (`raw` / `realistic` /
`clean`) had been read by nothing since October 2025 and was removed on 2026-09-28.

---

## 🗂️ Directory Structure

### Hierarchical Organization

```
data/processed/
├── ticks_index.parquet               # Tick index (derived, rebuilt from the files)
├── mt5/                              # broker_type: mt5
│   └── ticks/
│       ├── EURUSD/                   # one directory per symbol
│       │   ├── EURUSD_20250923_120000.parquet
│       │   └── EURUSD_20250923_130000.parquet
│       ├── GBPUSD/
│       └── USDJPY/
└── kraken_spot/                      # broker_type: kraken_spot
    └── ticks/
        └── BTCUSD/
            └── BTCUSD_20260124_141946.parquet
```

The current layout, with bars and the index files beside it, is described in
[Data Import Pipeline](data_import_pipeline.md).

### broker_type Field

**In JSON Metadata (TickCollector):**
```json
{
  "metadata": {
    "symbol": "EURUSD",
    "broker_type": "mt5",
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
    "broker_type": "mt5",      // Taken from JSON
    "broker": "Vantage...",
    ...
}
```

**Legacy files:** a file that names no `broker_type` is read through its legacy `data_collector`
field. A file carrying neither is refused, with a message listing the broker types
`market_config.json` knows; an unknown broker type is refused the same way.

---

## 📄 Import Behavior

### Normal Import
```bash
python python/cli/data_index_cli.py import

Processing: EURUSD_20250923_120000_ticks.json
  → broker_type: 'mt5'
  → Target: data/processed/mt5/ticks/EURUSD/
  → Checking for existing duplicates (all broker types)...
  → No duplicates found ✅
  → Creating: EURUSD_20250923_120000.parquet
  ✓ mt5/ticks/EURUSD/EURUSD_20250923_120000.parquet: 45,231 Ticks
    Compression 10.2:1 (45.3MB → 4.4MB)
```

### Re-Import Detection - Same Broker
```bash
python python/cli/data_index_cli.py import

Processing: EURUSD_20250923_120000_ticks.json
  → broker_type: 'mt5'
  → Target: data/processed/mt5/ticks/EURUSD/
  → Checking for existing duplicates (all broker types)...
  ⚠️  Found existing Parquet: mt5/EURUSD/EURUSD_20250923_120000.parquet
      Existing: broker_type='mt5' | Importing: broker_type='mt5'

ERROR:
================================================================================
⚠️  ARTIFICIAL DUPLICATE DETECTED - DATA INTEGRITY VIOLATION
================================================================================

📄 Original Source JSON:
   EURUSD_20250923_120000_ticks.json

📦 Duplicate Parquet Files Found: 1

   [1] mt5/ticks/EURUSD/EURUSD_20250923_120000.parquet
       Ticks:          45,231
       Range:     2025-09-23 12:00:00 → 2025-09-23 14:30:45
       Size:            4.42 MB

📋 Parquet Metadata Comparison:

   • source_file          ✅ IDENTICAL
   • symbol               ✅ IDENTICAL
   • broker_type          ✅ IDENTICAL
   • broker               ✅ IDENTICAL
   • data_format_version  ✅ IDENTICAL
   • tick_count           ✅ IDENTICAL
   • processed_at         ⚠️  DIFFERENT
       [1] 2025-09-23T12:05:30
       [2] 2025-09-23T14:32:15

🔬 Data Similarity Analysis:
   • Tick Counts:  ✅ IDENTICAL
   • Time Ranges:  ✅ IDENTICAL

⚠️  🔴 CRITICAL - Complete data duplication detected
   Impact: Identical files, test results will be severely compromised (2x tick density)

💡 Recommended Actions:
   1. DELETE the duplicate file (both are completely identical)
   2. Keep either file, they contain the exact same data
   3. Re-run the test after cleanup
   5. PREVENT: Never manually copy Parquet files in processed/ directory
   6. Rebuild index after cleanup

================================================================================

→ Skipping import (duplicate already exists)
```

`--override` deletes the existing file and imports again instead of skipping.

### Cross-Broker Duplicate Detection 🔥

```bash
# Scenario: The same source accidentally imported under a different broker_type

python python/cli/data_index_cli.py import

Processing: EURUSD_20250923_120000_ticks.json
  → broker_type: 'kraken_spot'  # Wrong broker in the file's metadata!
  → Target: data/processed/kraken_spot/ticks/EURUSD/
  → Checking for existing duplicates (all broker types)...
  ⚠️  Found existing Parquet: mt5/EURUSD/EURUSD_20250923_120000.parquet
      Existing: broker_type='mt5' | Importing: broker_type='kraken_spot'

ERROR:
================================================================================
⚠️  ARTIFICIAL DUPLICATE DETECTED - DATA INTEGRITY VIOLATION
================================================================================

📄 Original Source JSON:
   EURUSD_20250923_120000_ticks.json

📦 Duplicate Parquet Files Found: 1

   [1] mt5/ticks/EURUSD/EURUSD_20250923_120000.parquet
       Ticks:          45,231
       Range:     2025-09-23 12:00:00 → 2025-09-23 14:30:45
       Size:            4.42 MB

📋 Parquet Metadata Comparison:

   • source_file          ✅ IDENTICAL
   • symbol               ✅ IDENTICAL
   • broker_type          ⚠️  CROSS-BROKER DUPLICATE!
       [1] mt5
       [2] kraken_spot
   • broker               ✅ IDENTICAL
   • data_format_version  ✅ IDENTICAL

⚠️  🔴 CRITICAL - Cross-Broker Duplication
   Impact: Same data imported under different broker_types: mt5, kraken_spot

💡 Recommended Actions:
   1. INVESTIGATE why the same source was imported under different broker_types
   2. DELETE one of the duplicate files (choose the wrong collector)
   3. Check your import workflow to prevent cross-broker duplicates
   4. Rebuild index after cleanup

================================================================================
```

---

## 🚨 Artificial Duplicate Detection (Cross-Directory Protection)

### Import Prevention (tick_data_importer.py)
**Searches across ALL broker directories**

```python
search_pattern = f'*/ticks/{symbol}/{symbol}_*.parquet'
existing_files = list(self.target_dir.glob(search_pattern))
# Searches: data/processed/*/ticks/EURUSD/EURUSD_*.parquet
#   → mt5/ticks/EURUSD/*.parquet
#   → kraken_spot/ticks/EURUSD/*.parquet
#   → (all broker types)
```

**What it detects:**
```
Scenario 1: Same Broker Re-Import
────────────────────────────────────────
mt5/ticks/EURUSD/EURUSD_20250923_120000.parquet           (exists)
mt5/ticks/EURUSD/EURUSD_20250923_120000.parquet           (trying to import)
→ 🔴 DUPLICATE DETECTED (same broker)

Scenario 2: Cross-Broker Duplicate
────────────────────────────────────────
mt5/ticks/EURUSD/EURUSD_20250923_120000.parquet           (exists)
kraken_spot/ticks/EURUSD/EURUSD_20250923_120000.parquet   (trying to import)
→ 🔴 CROSS-BROKER DUPLICATE!
```

After a cleanup, rebuild the tick index: `python python/cli/tick_index_cli.py rebuild`.

### Metadata Comparison

The duplicate report also compares `broker_type`:

```
📋 Parquet Metadata Comparison:

   • source_file          ✅ IDENTICAL
   • symbol               ✅ IDENTICAL
   • broker_type          ⚠️  CROSS-BROKER DUPLICATE!
       [1] mt5
       [2] kraken_spot
   • broker               ✅ IDENTICAL
```

**Important:** `broker_type` is **NOT** used as a duplicate criterion!
- Duplicate = Same `source_file`
- `broker_type` is only displayed for **informational purposes**

### Signal import uses a different rule

This whole document describes the **tick** path. The signal importer (JSONL → parquet, see
[signal_data_source.md](signal_data_source.md)) does not search across broker directories and does not
read source metadata — it skips when the target parquet already exists, unless `--override`:

| | Tick import | Signal import |
|---|---|---|
| Question asked | "was this *source file* already imported, under any broker type?" | "does this *target file* already exist?" |
| Compared | `source_file` in the parquet metadata, globbed over `*/ticks/{symbol}/` | `target_path.exists()` |
| Reason | one JSON can land under the wrong broker directory, and that must be caught | the target path is derived from the source, so identity is already in the name |

Do not carry assumptions from one to the other — a signal re-import under a changed path
would not be detected as a duplicate the way a tick re-import is.
