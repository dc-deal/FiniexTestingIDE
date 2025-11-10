//+------------------------------------------------------------------+
//| BrokerConfigExporter.mq5                                          |
//| Exports STATIC broker configuration and symbol specifications    |
//| For FiniexTestingIDE - Trade Simulation realistic configuration  |
//| VERSION 2.0 - RAW DATA ONLY, ALL SYMBOLS                         |
//+------------------------------------------------------------------+
#property copyright "FiniexTestingIDE"
#property version "2.00"
#property script_show_inputs

// Input parameters
input string ExportFileName = "broker_config.json";  // Output filename

//+------------------------------------------------------------------+
//| Script program start function                                    |
//+------------------------------------------------------------------+
void OnStart()
{
    Print("=== Broker Config Exporter v2.0 Started ===");
    Print("NOTE: Exporting ALL symbols from broker (raw data only)");
    Print("      Dynamic properties excluded.");

    // Open file for writing
    int fileHandle = FileOpen(ExportFileName, FILE_WRITE | FILE_TXT | FILE_ANSI);

    if (fileHandle == INVALID_HANDLE)
    {
        Print("ERROR: Failed to create file: ", ExportFileName);
        Print("Error code: ", GetLastError());
        return;
    }

    Print("File opened successfully: ", ExportFileName);

    // Build JSON structure
    WriteString(fileHandle, "{");
    WriteString(fileHandle, "  \"_comment\": \"Static broker configuration for FiniexTestingIDE - Raw data only\",");
    WriteString(fileHandle, "  \"_version\": \"2.0\",");
    
    // Export info with symbol counts
    int totalSymbols = SymbolsTotal(false);
    WriteString(fileHandle, "  \"export_info\": {");
    WriteString(fileHandle, "    \"timestamp\": \"" + TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS) + "\",");
    WriteString(fileHandle, "    \"mt5_version\": \"" + TerminalInfoString(TERMINAL_NAME) + " " + IntegerToString(TerminalInfoInteger(TERMINAL_BUILD)) + "\",");
    WriteString(fileHandle, "    \"exporter_version\": \"2.00\",");
    WriteString(fileHandle, "    \"symbols_total\": " + IntegerToString(totalSymbols));
    WriteString(fileHandle, "  },");

    // Broker information (STATIC)
    ExportBrokerInfo(fileHandle);
    WriteString(fileHandle, ",");

    // Trading permissions (STATIC)
    ExportTradingPermissions(fileHandle);
    WriteString(fileHandle, ",");

    // Symbol specifications (ALL SYMBOLS, STATIC ONLY)
    ExportAllSymbols(fileHandle);

    WriteString(fileHandle, "}");

    FileClose(fileHandle);

    Print("=== Export completed successfully ===");
    Print("File location: ", TerminalInfoString(TERMINAL_DATA_PATH), "\\MQL5\\Files\\", ExportFileName);
    Alert("Broker config exported to: " + ExportFileName);
}

//+------------------------------------------------------------------+
//| Export STATIC broker company and server information             |
//+------------------------------------------------------------------+
void ExportBrokerInfo(int handle)
{
    WriteString(handle, "  \"broker_info\": {");
    WriteString(handle, "    \"company\": \"" + AccountInfoString(ACCOUNT_COMPANY) + "\",");
    WriteString(handle, "    \"server\": \"" + AccountInfoString(ACCOUNT_SERVER) + "\",");
    WriteString(handle, "    \"name\": \"demo_account\",");

    // Trade mode (0=Real, 1=Contest, 2=Demo)
    int tradeMode = (int)AccountInfoInteger(ACCOUNT_TRADE_MODE);
    string tradeModeStr = "unknown";
    if (tradeMode == ACCOUNT_TRADE_MODE_DEMO)
        tradeModeStr = "demo";
    else if (tradeMode == ACCOUNT_TRADE_MODE_CONTEST)
        tradeModeStr = "contest";
    else if (tradeMode == ACCOUNT_TRADE_MODE_REAL)
        tradeModeStr = "real";

    WriteString(handle, "    \"trade_mode\": \"" + tradeModeStr + "\",");
    WriteString(handle, "    \"leverage\": " + IntegerToString(AccountInfoInteger(ACCOUNT_LEVERAGE)) + ",");

    // Margin mode
    int marginMode = (int)AccountInfoInteger(ACCOUNT_MARGIN_MODE);
    string marginModeStr = "unknown";
    if (marginMode == ACCOUNT_MARGIN_MODE_RETAIL_NETTING)
        marginModeStr = "retail_netting";
    else if (marginMode == ACCOUNT_MARGIN_MODE_EXCHANGE)
        marginModeStr = "exchange";
    else if (marginMode == ACCOUNT_MARGIN_MODE_RETAIL_HEDGING)
        marginModeStr = "retail_hedging";

    WriteString(handle, "    \"margin_mode\": \"" + marginModeStr + "\",");

    // Stop out mode (0=Percent, 1=Money)
    int stopoutMode = (int)AccountInfoInteger(ACCOUNT_MARGIN_SO_MODE);
    string stopoutModeStr = (stopoutMode == ACCOUNT_STOPOUT_MODE_PERCENT) ? "percent" : "money";

    WriteString(handle, "    \"stopout_mode\": \"" + stopoutModeStr + "\",");
    WriteString(handle, "    \"stopout_level\": " + DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN_SO_SO), 2) + ",");
    WriteString(handle, "    \"margin_call_level\": " + DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN_SO_CALL), 2) + ",");
    WriteString(handle, "    \"hedging_allowed\": " + BoolToString(marginMode == ACCOUNT_MARGIN_MODE_RETAIL_HEDGING));
    WriteString(handle, "  }");
}

//+------------------------------------------------------------------+
//| Export STATIC trading permissions and capabilities              |
//+------------------------------------------------------------------+
void ExportTradingPermissions(int handle)
{
    WriteString(handle, "  \"trading_permissions\": {");
    WriteString(handle, "    \"trade_allowed\": " + BoolToString(AccountInfoInteger(ACCOUNT_TRADE_ALLOWED)) + ",");
    WriteString(handle, "    \"expert_allowed\": " + BoolToString(AccountInfoInteger(ACCOUNT_TRADE_EXPERT)) + ",");
    WriteString(handle, "    \"limit_orders\": " + IntegerToString(AccountInfoInteger(ACCOUNT_LIMIT_ORDERS)) + ",");

    // Check available order types
    WriteString(handle, "    \"order_types\": {");
    WriteString(handle, "      \"market\": true,");
    WriteString(handle, "      \"limit\": true,");
    WriteString(handle, "      \"stop\": true,");
    WriteString(handle, "      \"stop_limit\": true");
    WriteString(handle, "    }");
    WriteString(handle, "  }");
}

//+------------------------------------------------------------------+
//| Export ALL symbols from broker (RAW DATA ONLY)                  |
//+------------------------------------------------------------------+
void ExportAllSymbols(int handle)
{
    WriteString(handle, "  \"symbols\": {");

    int total = SymbolsTotal(false);  // false = ALL symbols from broker
    
    Print("Total symbols available from broker: ", total);
    Print("Starting export of all symbols...");

    // First pass: Collect all symbols into array for sorting
    string symbols[];
    ArrayResize(symbols, total);
    int validCount = 0;

    for (int i = 0; i < total; i++)
    {
        string symbol = SymbolName(i, false);
        
        // Ensure symbol is selectable
        if (!SymbolSelect(symbol, true))
        {
            Print("WARNING: Cannot select symbol: ", symbol);
            continue;
        }
        
        // Check if trading is available (skip broken/disabled symbols)
        int tradeMode = (int)SymbolInfoInteger(symbol, SYMBOL_TRADE_MODE);
        if (tradeMode == SYMBOL_TRADE_MODE_DISABLED)
        {
            Print("SKIPPED: Trading disabled for: ", symbol);
            continue;
        }
        
        symbols[validCount] = symbol;
        validCount++;
        
        // Progress indicator
        if ((i + 1) % 50 == 0)
            Print("Scanning symbols: ", i + 1, "/", total, " (", validCount, " valid)");
    }

    // Trim array to actual size
    ArrayResize(symbols, validCount);
    
    // Sort alphabetically
    ArraySort(symbols);
    
    Print("Validated ", validCount, " symbols. Starting export...");

    // Second pass: Export sorted symbols
    bool firstSymbol = true;
    for (int i = 0; i < validCount; i++)
    {
        if (!firstSymbol)
            WriteString(handle, ",");
        firstSymbol = false;

        ExportSymbolInfo(handle, symbols[i]);
        
        // Progress indicator
        if ((i + 1) % 50 == 0)
            Print("Exporting: ", i + 1, "/", validCount, " (", ((i + 1) * 100 / validCount), "%)");
    }

    WriteString(handle, "  }");

    Print("=== Export Statistics ===");
    Print("Total symbols from broker: ", total);
    Print("Successfully exported: ", validCount);
    Print("Skipped/Invalid: ", total - validCount);
}

//+------------------------------------------------------------------+
//| Export STATIC information for a single symbol (RAW DATA)        |
//+------------------------------------------------------------------+
void ExportSymbolInfo(int handle, string symbol)
{
    WriteString(handle, "    \"" + symbol + "\": {");

    // Broker categorization path (RAW DATA from broker)
    string path = SymbolInfoString(symbol, SYMBOL_PATH);
    StringReplace(path, "\\", "\\\\");  
    WriteString(handle, "      \"path\": \"" + path + "\",");

    // Basic info (RAW DATA)
    WriteString(handle, "      \"description\": \"" + SymbolInfoString(symbol, SYMBOL_DESCRIPTION) + "\",");
    WriteString(handle, "      \"base_currency\": \"" + SymbolInfoString(symbol, SYMBOL_CURRENCY_BASE) + "\",");
    WriteString(handle, "      \"profit_currency\": \"" + SymbolInfoString(symbol, SYMBOL_CURRENCY_PROFIT) + "\",");
    WriteString(handle, "      \"margin_currency\": \"" + SymbolInfoString(symbol, SYMBOL_CURRENCY_MARGIN) + "\",");

    // Trading mode (RAW DATA)
    int tradeMode = (int)SymbolInfoInteger(symbol, SYMBOL_TRADE_MODE);
    string tradeModeStr = "unknown";
    if (tradeMode == SYMBOL_TRADE_MODE_DISABLED)
        tradeModeStr = "disabled";
    else if (tradeMode == SYMBOL_TRADE_MODE_LONGONLY)
        tradeModeStr = "long_only";
    else if (tradeMode == SYMBOL_TRADE_MODE_SHORTONLY)
        tradeModeStr = "short_only";
    else if (tradeMode == SYMBOL_TRADE_MODE_CLOSEONLY)
        tradeModeStr = "close_only";
    else if (tradeMode == SYMBOL_TRADE_MODE_FULL)
        tradeModeStr = "full";

    WriteString(handle, "      \"trade_mode\": \"" + tradeModeStr + "\",");
    WriteString(handle, "      \"trade_allowed\": " + BoolToString(tradeMode != SYMBOL_TRADE_MODE_DISABLED) + ",");

    // Volume (lot) specifications - STATIC
    WriteString(handle, "      \"volume_min\": " + DoubleToString(SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN), 2) + ",");
    WriteString(handle, "      \"volume_max\": " + DoubleToString(SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX), 2) + ",");
    WriteString(handle, "      \"volume_step\": " + DoubleToString(SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP), 2) + ",");
    WriteString(handle, "      \"volume_limit\": " + DoubleToString(SymbolInfoDouble(symbol, SYMBOL_VOLUME_LIMIT), 2) + ",");

    // Contract specifications - STATIC
    WriteString(handle, "      \"contract_size\": " + DoubleToString(SymbolInfoDouble(symbol, SYMBOL_TRADE_CONTRACT_SIZE), 0) + ",");
    WriteString(handle, "      \"tick_size\": " + DoubleToString(SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_SIZE), (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS)) + ",");
    WriteString(handle, "      \"point\": " + DoubleToString(SymbolInfoDouble(symbol, SYMBOL_POINT), (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS)) + ",");
    WriteString(handle, "      \"digits\": " + IntegerToString(SymbolInfoInteger(symbol, SYMBOL_DIGITS)) + ",");

    // Spread configuration - STATIC
    WriteString(handle, "      \"spread_float\": " + BoolToString(SymbolInfoInteger(symbol, SYMBOL_SPREAD_FLOAT)) + ",");

    // Swap (rollover) - STATIC
    int swapMode = (int)SymbolInfoInteger(symbol, SYMBOL_SWAP_MODE);
    string swapModeStr = "unknown";
    if (swapMode == SYMBOL_SWAP_MODE_DISABLED)
        swapModeStr = "disabled";
    else if (swapMode == SYMBOL_SWAP_MODE_POINTS)
        swapModeStr = "points";
    else if (swapMode == SYMBOL_SWAP_MODE_CURRENCY_SYMBOL)
        swapModeStr = "currency_symbol";
    else if (swapMode == SYMBOL_SWAP_MODE_CURRENCY_MARGIN)
        swapModeStr = "currency_margin";
    else if (swapMode == SYMBOL_SWAP_MODE_CURRENCY_DEPOSIT)
        swapModeStr = "currency_deposit";
    else if (swapMode == SYMBOL_SWAP_MODE_INTEREST_CURRENT)
        swapModeStr = "interest_current";
    else if (swapMode == SYMBOL_SWAP_MODE_INTEREST_OPEN)
        swapModeStr = "interest_open";
    else if (swapMode == SYMBOL_SWAP_MODE_REOPEN_CURRENT)
        swapModeStr = "reopen_current";
    else if (swapMode == SYMBOL_SWAP_MODE_REOPEN_BID)
        swapModeStr = "reopen_bid";

    WriteString(handle, "      \"swap_mode\": \"" + swapModeStr + "\",");
    WriteString(handle, "      \"swap_long\": " + DoubleToString(SymbolInfoDouble(symbol, SYMBOL_SWAP_LONG), 2) + ",");
    WriteString(handle, "      \"swap_short\": " + DoubleToString(SymbolInfoDouble(symbol, SYMBOL_SWAP_SHORT), 2) + ",");
    WriteString(handle, "      \"swap_rollover3days\": " + IntegerToString(SymbolInfoInteger(symbol, SYMBOL_SWAP_ROLLOVER3DAYS)) + ",");

    // Margin requirements - STATIC
    WriteString(handle, "      \"margin_initial\": " + DoubleToString(SymbolInfoDouble(symbol, SYMBOL_MARGIN_INITIAL), 2) + ",");
    WriteString(handle, "      \"margin_maintenance\": " + DoubleToString(SymbolInfoDouble(symbol, SYMBOL_MARGIN_MAINTENANCE), 2) + ",");
    WriteString(handle, "      \"margin_long\": " + DoubleToString(SymbolInfoDouble(symbol, SYMBOL_MARGIN_LONG), 2) + ",");
    WriteString(handle, "      \"margin_short\": " + DoubleToString(SymbolInfoDouble(symbol, SYMBOL_MARGIN_SHORT), 2) + ",");
    WriteString(handle, "      \"margin_limit\": " + DoubleToString(SymbolInfoDouble(symbol, SYMBOL_MARGIN_LIMIT), 2) + ",");
    WriteString(handle, "      \"margin_stop\": " + DoubleToString(SymbolInfoDouble(symbol, SYMBOL_MARGIN_STOP), 2) + ",");
    WriteString(handle, "      \"margin_stop_limit\": " + DoubleToString(SymbolInfoDouble(symbol, SYMBOL_MARGIN_STOPLIMIT), 2) + ",");

    // Stops level - STATIC
    int stopsLevel = (int)SymbolInfoInteger(symbol, SYMBOL_TRADE_STOPS_LEVEL);
    int freezeLevel = (int)SymbolInfoInteger(symbol, SYMBOL_TRADE_FREEZE_LEVEL);

    WriteString(handle, "      \"stops_level\": " + IntegerToString(stopsLevel) + ",");
    WriteString(handle, "      \"freeze_level\": " + IntegerToString(freezeLevel));

    // ============================================================================
    // RAW DATA ONLY - NO DYNAMIC PROPERTIES (v2.0):
    // Removed: tick_value, tick_value_profit, tick_value_loss (dynamic)
    // Removed: bid, ask (dynamic market data)
    // Removed: spread_current (dynamic)
    // Removed: session data (snapshots)
    // Removed: volume data (snapshots)
    // Removed: trading_hours_active (time-dependent)
    // ============================================================================

    WriteString(handle, "    }");
}

//+------------------------------------------------------------------+
//| Helper: Write string to file with newline                        |
//+------------------------------------------------------------------+
void WriteString(int handle, string text)
{
    FileWriteString(handle, text + "\n");
}

//+------------------------------------------------------------------+
//| Helper: Convert bool to JSON string                              |
//+------------------------------------------------------------------+
string BoolToString(bool value)
{
    return value ? "true" : "false";
}
