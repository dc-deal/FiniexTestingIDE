# Data Directory

This directory contains tick data in various processing stages.

## Structure

```
data/
├── raw/           # MQL5 JSON exports (gitignored)
├── processed/     # Optimized Parquet files (gitignored)  
└── cache/         # Temporary processing files (gitignored)
```

## Data Flow

1. **MQL5 Export** → `raw/` (JSON files)
2. **Python Import** → `processed/` (Parquet files)
3. **Testing Engine** → `cache/` (Memory-mapped access)

## File Sizes

- **Raw JSON**: 300-900MB for 2-day EURUSD
- **Processed Parquet**: 30-90MB (10:1 compression)
- **Cache**: Variable based on active tests

**Note:** All data files are excluded from Git via .gitignore for privacy and size reasons.
