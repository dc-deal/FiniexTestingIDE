# MQL5 Data Collection

## TickCollector.mq5

Professional tick data collector for MetaTrader 5.

### Features
- ✅ High-frequency tick collection
- ✅ JSON export format
- ✅ Automatic file rotation
- ✅ Session classification
- ✅ Spread calculation
- ✅ Real volume support

### Installation

1. Copy `TickCollector.mq5` to your MetaTrader 5 Experts folder
2. Compile in MetaEditor (F7)
3. Attach to any EURUSD chart
4. Configure export path: `C:\FinexData\`

### Expected Output

**2-Day EURUSD Collection:**
- Files: 10-20 JSON files
- Total size: 300-900MB
- Compression ratio: ~10:1 after Parquet conversion

### Troubleshooting

**No files created?**
- Check Expert Advisor logs in Terminal
- Verify export path exists
- Ensure AutoTrading is enabled

**Large file sizes?**
- Reduce MaxTicksPerFile parameter
- Consider filtering by session