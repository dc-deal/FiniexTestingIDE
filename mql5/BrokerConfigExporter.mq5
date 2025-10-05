//+------------------------------------------------------------------+
//| BrokerConfigExporter.mq5                                          |
//| Exports broker configuration and symbol specifications as JSON    |
//| For FiniexTestingIDE - Trade Simulation realistic configuration  |
//+------------------------------------------------------------------+
#property copyright "FiniexTestingIDE"
#property version "1.00"
#property script_show_inputs

// Input parameters
input string ExportFileName = "broker_config.json";                  // Output filename
input string SymbolsToExport = "EURUSD,GBPUSD,USDJPY,AUDUSD,BTCUSD"; // Comma-separated symbols

//+------------------------------------------------------------------+
//| Script program start function                                    |
//+------------------------------------------------------------------+
void OnStart()
{
    Print("=== Broker Config Exporter Started ===");

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
    WriteString(fileHandle, "  \"_comment\": \"Demo account configuration - anonymized sample data for FiniexTestingIDE\",");
    WriteString(fileHandle, "  \"export_info\": {");
    WriteString(fileHandle, "    \"timestamp\": \"" + TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS) + "\",");
    WriteString(fileHandle, "    \"mt5_version\": \"" + TerminalInfoString(TERMINAL_NAME) + " " + IntegerToString(TerminalInfoInteger(TERMINAL_BUILD)) + "\",");
    WriteString(fileHandle, "    \"exporter_version\": \"1.00\"");
    WriteString(fileHandle, "  },");

    // Broker information
    ExportBrokerInfo(fileHandle);
    WriteString(fileHandle, ",");

    // Account information
    ExportAccountInfo(fileHandle);
    WriteString(fileHandle, ",");

    // Trading permissions
    ExportTradingPermissions(fileHandle);
    WriteString(fileHandle, ",");

    // Symbol specifications
    ExportSymbols(fileHandle, SymbolsToExport);

    WriteString(fileHandle, "}");

    FileClose(fileHandle);

    Print("=== Export completed successfully ===");
    Print("File location: ", TerminalInfoString(TERMINAL_DATA_PATH), "\\MQL5\\Files\\", ExportFileName);
    Alert("Broker config exported to: " + ExportFileName);
}

//+------------------------------------------------------------------+
//| Export broker company and server information                     |
//+------------------------------------------------------------------+
void ExportBrokerInfo(int handle)
{
    WriteString(handle, "  \"broker_info\": {");
    WriteString(handle, "    \"company\": \"" + AccountInfoString(ACCOUNT_COMPANY) + "\",");
    WriteString(handle, "    \"server\": \"" + AccountInfoString(ACCOUNT_SERVER) + "\",");
    WriteString(handle, "    \"name\": \"demo_account\","); // statt echten Namen

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
//| Export account information                                        |
//+------------------------------------------------------------------+
void ExportAccountInfo(int handle)
{
    WriteString(handle, "  \"account_info\": {");
    WriteString(handle, "    \"account_number\": 99999999,");
    WriteString(handle, "    \"currency\": \"" + AccountInfoString(ACCOUNT_CURRENCY) + "\",");
    WriteString(handle, "    \"balance\": " + DoubleToString(AccountInfoDouble(ACCOUNT_BALANCE), 2) + ",");
    WriteString(handle, "    \"equity\": " + DoubleToString(AccountInfoDouble(ACCOUNT_EQUITY), 2) + ",");
    WriteString(handle, "    \"margin\": " + DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN), 2) + ",");
    WriteString(handle, "    \"free_margin\": " + DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN_FREE), 2) + ",");
    WriteString(handle, "    \"margin_level\": " + DoubleToString(AccountInfoDouble(ACCOUNT_MARGIN_LEVEL), 2));
    WriteString(handle, "  }");
}

//+------------------------------------------------------------------+
//| Export trading permissions and capabilities                      |
//+------------------------------------------------------------------+
void ExportTradingPermissions(int handle)
{
    WriteString(handle, "  \"trading_permissions\": {");
    WriteString(handle, "    \"trade_allowed\": " + BoolToString(AccountInfoInteger(ACCOUNT_TRADE_ALLOWED)) + ",");
    WriteString(handle, "    \"expert_allowed\": " + BoolToString(AccountInfoInteger(ACCOUNT_TRADE_EXPERT)) + ",");
    WriteString(handle, "    \"limit_orders\": " + IntegerToString(AccountInfoInteger(ACCOUNT_LIMIT_ORDERS)) + ",");

    // Check available order types
    WriteString(handle, "    \"order_types\": {");
    WriteString(handle, "      \"market\": true,");    // Always available
    WriteString(handle, "      \"limit\": true,");     // Always available
    WriteString(handle, "      \"stop\": true,");      // Always available
    WriteString(handle, "      \"stop_limit\": true"); // Always available in MT5
    WriteString(handle, "    }");
    WriteString(handle, "  }");
}

//+------------------------------------------------------------------+
//| Export symbol specifications                                      |
//+------------------------------------------------------------------+
void ExportSymbols(int handle, string symbolList)
{
    WriteString(handle, "  \"symbols\": {");

    string symbols[];
    int count = StringSplit(symbolList, StringGetCharacter(",", 0), symbols);

    bool firstSymbol = true;

    for (int i = 0; i < count; i++)
    {
        string symbol = symbols[i];
        StringTrimLeft(symbol);
        StringTrimRight(symbol);

        // Check if symbol exists and is visible
        if (!SymbolSelect(symbol, true))
        {
            Print("WARNING: Symbol not found or cannot be selected: ", symbol);
            continue;
        }

        if (!firstSymbol)
            WriteString(handle, ",");
        firstSymbol = false;

        ExportSymbolInfo(handle, symbol);
    }

    WriteString(handle, "  }");
}

//+------------------------------------------------------------------+
//| Export detailed information for a single symbol                  |
//+------------------------------------------------------------------+
void ExportSymbolInfo(int handle, string symbol)
{
    WriteString(handle, "    \"" + symbol + "\": {");

    // Basic info
    WriteString(handle, "      \"description\": \"" + SymbolInfoString(symbol, SYMBOL_DESCRIPTION) + "\",");
    WriteString(handle, "      \"base_currency\": \"" + SymbolInfoString(symbol, SYMBOL_CURRENCY_BASE) + "\",");
    WriteString(handle, "      \"profit_currency\": \"" + SymbolInfoString(symbol, SYMBOL_CURRENCY_PROFIT) + "\",");
    WriteString(handle, "      \"margin_currency\": \"" + SymbolInfoString(symbol, SYMBOL_CURRENCY_MARGIN) + "\",");

    // Trading mode
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

    // Volume (lot) specifications
    WriteString(handle, "      \"volume_min\": " + DoubleToString(SymbolInfoDouble(symbol, SYMBOL_VOLUME_MIN), 2) + ",");
    WriteString(handle, "      \"volume_max\": " + DoubleToString(SymbolInfoDouble(symbol, SYMBOL_VOLUME_MAX), 2) + ",");
    WriteString(handle, "      \"volume_step\": " + DoubleToString(SymbolInfoDouble(symbol, SYMBOL_VOLUME_STEP), 2) + ",");
    WriteString(handle, "      \"volume_limit\": " + DoubleToString(SymbolInfoDouble(symbol, SYMBOL_VOLUME_LIMIT), 2) + ",");

    // Contract specifications
    WriteString(handle, "      \"contract_size\": " + DoubleToString(SymbolInfoDouble(symbol, SYMBOL_TRADE_CONTRACT_SIZE), 0) + ",");
    WriteString(handle, "      \"tick_size\": " + DoubleToString(SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_SIZE), (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS)) + ",");
    WriteString(handle, "      \"tick_value\": " + DoubleToString(SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_VALUE), 5) + ",");
    WriteString(handle, "      \"tick_value_profit\": " + DoubleToString(SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_VALUE_PROFIT), 5) + ",");
    WriteString(handle, "      \"tick_value_loss\": " + DoubleToString(SymbolInfoDouble(symbol, SYMBOL_TRADE_TICK_VALUE_LOSS), 5) + ",");
    WriteString(handle, "      \"point\": " + DoubleToString(SymbolInfoDouble(symbol, SYMBOL_POINT), (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS)) + ",");
    WriteString(handle, "      \"digits\": " + IntegerToString(SymbolInfoInteger(symbol, SYMBOL_DIGITS)) + ",");

    // Current prices
    WriteString(handle, "      \"bid\": " + DoubleToString(SymbolInfoDouble(symbol, SYMBOL_BID), (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS)) + ",");
    WriteString(handle, "      \"ask\": " + DoubleToString(SymbolInfoDouble(symbol, SYMBOL_ASK), (int)SymbolInfoInteger(symbol, SYMBOL_DIGITS)) + ",");

    // Spread
    int spreadCurrent = (int)SymbolInfoInteger(symbol, SYMBOL_SPREAD);
    WriteString(handle, "      \"spread_current\": " + IntegerToString(spreadCurrent) + ",");
    WriteString(handle, "      \"spread_float\": " + BoolToString(SymbolInfoInteger(symbol, SYMBOL_SPREAD_FLOAT)) + ",");

    // Swap (rollover)
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

    // Margin requirements
    WriteString(handle, "      \"margin_initial\": " + DoubleToString(SymbolInfoDouble(symbol, SYMBOL_MARGIN_INITIAL), 2) + ",");
    WriteString(handle, "      \"margin_maintenance\": " + DoubleToString(SymbolInfoDouble(symbol, SYMBOL_MARGIN_MAINTENANCE), 2) + ",");
    WriteString(handle, "      \"margin_long\": " + DoubleToString(SymbolInfoDouble(symbol, SYMBOL_MARGIN_LONG), 2) + ",");
    WriteString(handle, "      \"margin_short\": " + DoubleToString(SymbolInfoDouble(symbol, SYMBOL_MARGIN_SHORT), 2) + ",");
    WriteString(handle, "      \"margin_limit\": " + DoubleToString(SymbolInfoDouble(symbol, SYMBOL_MARGIN_LIMIT), 2) + ",");
    WriteString(handle, "      \"margin_stop\": " + DoubleToString(SymbolInfoDouble(symbol, SYMBOL_MARGIN_STOP), 2) + ",");
    WriteString(handle, "      \"margin_stop_limit\": " + DoubleToString(SymbolInfoDouble(symbol, SYMBOL_MARGIN_STOPLIMIT), 2) + ",");

    // Trading time
    datetime from, to;
    bool tradingAllowed = false;
    datetime currentTime = TimeCurrent();
    MqlDateTime dt;
    TimeToStruct(currentTime, dt);
    int dayOfWeek = dt.day_of_week; // 0=Sunday, 1=Monday, etc.
    if (SymbolInfoSessionTrade(symbol, (ENUM_DAY_OF_WEEK)dayOfWeek, 0, from, to))
    {
        tradingAllowed = (currentTime >= from && currentTime <= to);
    }
    WriteString(handle, "      \"trading_hours_active\": " + BoolToString(tradingAllowed) + ",");

    // Stops level
    int stopsLevel = (int)SymbolInfoInteger(symbol, SYMBOL_TRADE_STOPS_LEVEL);
    int freezeLevel = (int)SymbolInfoInteger(symbol, SYMBOL_TRADE_FREEZE_LEVEL);

    WriteString(handle, "      \"stops_level\": " + IntegerToString(stopsLevel) + ",");
    WriteString(handle, "      \"freeze_level\": " + IntegerToString(freezeLevel) + ",");

    // Session info
    WriteString(handle, "      \"session_deals\": " + IntegerToString(SymbolInfoInteger(symbol, SYMBOL_SESSION_DEALS)) + ",");
    WriteString(handle, "      \"session_buy_orders\": " + IntegerToString(SymbolInfoInteger(symbol, SYMBOL_SESSION_BUY_ORDERS)) + ",");
    WriteString(handle, "      \"session_sell_orders\": " + IntegerToString(SymbolInfoInteger(symbol, SYMBOL_SESSION_SELL_ORDERS)) + ",");

    // Volume (trading activity)
    WriteString(handle, "      \"volume\": " + IntegerToString(SymbolInfoInteger(symbol, SYMBOL_VOLUME)) + ",");
    WriteString(handle, "      \"volumehigh\": " + IntegerToString(SymbolInfoInteger(symbol, SYMBOL_VOLUMEHIGH)) + ",");
    WriteString(handle, "      \"volumelow\": " + IntegerToString(SymbolInfoInteger(symbol, SYMBOL_VOLUMELOW)));

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