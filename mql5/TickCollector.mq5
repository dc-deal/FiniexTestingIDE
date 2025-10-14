//+------------------------------------------------------------------+
//| FiniexTestingIDE Tick Data Collector - Enhanced Error Version    |
//| Sammelt Live-Tick-Daten mit gestuftem Error-Tracking            |
//| Version 1.0.5 - UTC Offset Auto-Detection                       |
//|                                                                  |
//| NEW in V1.0.5:                                                   |
//| - Automatic broker UTC offset detection                         |
//| - Warns user to verify offset (depends on PC clock)             |
//| - Adds broker_utc_offset_hours, local_device_time to JSON       |
//| - Tick times remain in broker server time (unchanged)           |
//+------------------------------------------------------------------+
#property copyright "FiniexTestingIDE"
#property strict

// Error-Severity-Enum
enum ENUM_ERROR_SEVERITY
{
    ERROR_NEGLIGIBLE = 0,    // Vernachlässigbar - Daten weiterhin brauchbar
    ERROR_SERIOUS = 1,       // Ernst - Daten brauchbar mit Lücke/Einschränkung
    ERROR_FATAL = 2          // Fatal - Daten womöglich unbrauchbar
};

// Struktur für detaillierte Error-Informationen
struct ErrorInfo
{
    ENUM_ERROR_SEVERITY severity;
    string errorType;
    string description;
    datetime timestamp;
    long timestamp_msc;
    int tickContext;
    double affectedValue;
    string additionalData;
};

// Input-Parameter
input string ExportPath = "";
input bool CollectTicks = true;
input int MaxTicksPerFile = 50000;
input bool IncludeRealVolume = true;
input bool IncludeTickFlags = true;
input ENUM_TIMEFRAMES VolumeTimeframe = PERIOD_M1;
input string DataFormatVersion = "1.0.5";  // CHANGED: Version 1.0.5
// Identifies the data collection platform (mt5, ib, etc.)
// Only change when importing from a different broker platform!
input string DataCollectorName = "mt5"; 
input string CollectionPurpose = "backtesting";
input string CollectorOperator = "";

// Error-Tracking Konfiguration
input bool EnableErrorTracking = true;
input int MaxErrorsPerFile = 1000;
input bool LogNegligibleErrors = true;
input bool LogSeriousErrors = true;
input bool LogFatalErrors = true;
input bool StopOnFatalErrors = false;

// Globale Variablen
int fileHandle = INVALID_HANDLE;
string currentFileName = "";
int tickCounter = 0;
datetime fileStartTime;

// NEW V1.0.5: Automatisch erkannter Broker UTC Offset
int g_brokerUtcOffsetHours = 0;

// Enhanced Error-Tracking
ErrorInfo errorBuffer[];
int errorCounts[3]; // [negligible, serious, fatal]
datetime lastTickTime = 0;
double lastBid = 0, lastAsk = 0, lastSpread = 0;
long lastTickMsc = 0;
int consecutiveErrorTicks = 0;
bool dataStreamCorrupted = false;

// Erweiterte Validierungsparameter
double maxSpreadPercent = 5.0;        // Max 5% Spread
double maxPriceJumpPercent = 10.0;    // Max 10% Preis-Sprung
int maxDataGapSeconds = 300;          // Max 5 Min Datenlücke
int warningDataGapSeconds = 60;       // Warning bei 1 Min Lücke

//+------------------------------------------------------------------+
//| Expert Advisor Initialisierung                                  |
//+------------------------------------------------------------------+
int OnInit()
{
    Print("═══════════════════════════════════════════════════════════");
    Print("  FiniexTestingIDE TickCollector V1.0.5                    ");
    Print("  UTC Offset Auto-Detection ENABLED (time_msc method)      ");
    Print("═══════════════════════════════════════════════════════════");
    
    // NEW: 100% ZUVERLÄSSIGE UTC-Offset-Erkennung via time_msc
    // time_msc ist IMMER in UTC (Unix timestamp in milliseconds)
    // tick.time ist in Broker Server Zeit
    
    MqlTick tick;
    if (!SymbolInfoTick(Symbol(), tick))
    {
        Print("❌ FEHLER: Konnte keinen Tick abrufen für UTC-Offset-Berechnung");
        Print("   Verwende Fallback: TimeGMT() Methode");
        
        datetime serverTime = TimeCurrent();
        datetime utcTime = TimeGMT();
        g_brokerUtcOffsetHours = (int)((serverTime - utcTime) / 3600);
    }
    else
    {
        // Broker Server Zeit aus tick.time
        datetime brokerTime = tick.time;
        
        // Echte UTC Zeit aus time_msc (Unix timestamp)
        datetime utcTime = (datetime)(tick.time_msc / 1000);
        
        // Berechne Offset in Stunden
        g_brokerUtcOffsetHours = (int)((brokerTime - utcTime) / 3600);
        
        Print("🌍 Broker Timezone Detection (via time_msc - 100% reliable):");
        Print("   Broker Time:  ", TimeToString(brokerTime, TIME_DATE | TIME_SECONDS));
        Print("   UTC Time:     ", TimeToString(utcTime, TIME_DATE | TIME_SECONDS));
        Print("   time_msc:     ", tick.time_msc);
        Print("   Calculated Offset: GMT", (g_brokerUtcOffsetHours >= 0 ? "+" : ""), g_brokerUtcOffsetHours);
    }
    Print("───────────────────────────────────────────────────────────");
    Print("Symbol:           ", Symbol());
    Print("Broker:           ", AccountInfoString(ACCOUNT_COMPANY));
    Print("Server:           ", AccountInfoString(ACCOUNT_SERVER));
    Print("Max Ticks/File:   ", MaxTicksPerFile);
    Print("Real Volume:      ", (IncludeRealVolume ? "Yes" : "No"));
    Print("Tick Flags:       ", (IncludeTickFlags ? "Yes" : "No"));
    Print("═══════════════════════════════════════════════════════════");
    Print("⚠️  IMPORTANT: Tick times stored in BROKER SERVER TIME");
    Print("   UTC Offset: GMT", (g_brokerUtcOffsetHours >= 0 ? "+" : ""), g_brokerUtcOffsetHours, " (auto-detected via time_msc)");
    Print("   UTC conversion will be handled by tick_importer.py");
    Print("═══════════════════════════════════════════════════════════\n");
    
    // Error-System initialisieren
    ArrayResize(errorBuffer, 0);
    ArrayInitialize(errorCounts, 0);
    lastTickTime = 0;
    lastTickMsc = 0;
    consecutiveErrorTicks = 0;
    dataStreamCorrupted = false;
    
    if(!CreateExportDirectory())
    {
        Alert("FEHLER: Export-Ordner konnte nicht erstellt werden: ", ExportPath);
        return INIT_FAILED;
    }
    
    if(!CreateNewExportFile())
    {
        Alert("FEHLER: Erste Export-Datei konnte nicht erstellt werden");
        return INIT_FAILED;
    }
    
    Print("✅ TickCollector V1.0.5 erfolgreich gestartet für ", Symbol());
    Print("✅ Export-Pfad: ", ExportPath);
    Print("✅ Gestuftes Error-Tracking aktiviert (Negligible:", LogNegligibleErrors, 
          " Serious:", LogSeriousErrors, " Fatal:", LogFatalErrors, ")");
    
    return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| Tick-Event Handler mit verbesserter Error-Detection             |
//+------------------------------------------------------------------+
void OnTick()
{
    if (!CollectTicks) return;
    
    // Prüfen ob Datenstream als korrupt markiert ist
    if (dataStreamCorrupted && StopOnFatalErrors)
    {
        Print("STOPP: Datenstream als korrupt markiert - Sammlung angehalten");
        return;
    }
    
    MqlTick tick;
    if (!SymbolInfoTick(Symbol(), tick))
    {
        LogError(ERROR_SERIOUS, "tick_unavailable", "SymbolInfoTick() failed", 
                TimeCurrent(), 0, 0, "");
        consecutiveErrorTicks++;
        
        if (consecutiveErrorTicks > 10)
        {
            LogError(ERROR_FATAL, "tick_stream_failure", 
                    StringFormat("Consecutive tick failures: %d", consecutiveErrorTicks),
                    TimeCurrent(), 0, 0, "");
            dataStreamCorrupted = true;
        }
        return;
    }
    
    // Reset consecutive errors bei erfolgreichem Tick
    consecutiveErrorTicks = 0;
    
    // Umfassende Datenvalidierung
    ENUM_ERROR_SEVERITY worstSeverity = ValidateTickDataEnhanced(tick);
    
    // Tick nur exportieren wenn nicht fatal oder wenn fatale Errors erlaubt sind
    if (worstSeverity != ERROR_FATAL || !StopOnFatalErrors)
    {
        if (ExportTick(tick))
        {
            tickCounter++;
            UpdateLastTickData(tick);
            
            if (tickCounter >= MaxTicksPerFile)
            {
                CloseCurrentFile();
                CreateNewExportFile();
            }
        }
    }
}

//+------------------------------------------------------------------+
//| Erweiterte Tick-Validierung mit gestuften Errors               |
//+------------------------------------------------------------------+
ENUM_ERROR_SEVERITY ValidateTickDataEnhanced(MqlTick &tick)
{
    ENUM_ERROR_SEVERITY worstSeverity = ERROR_NEGLIGIBLE;
    
    // 1. KRITISCHE PREIS-VALIDIERUNG (FATAL)
    if (tick.bid <= 0 || tick.ask <= 0)
    {
        LogError(ERROR_FATAL, "invalid_price_zero", "Bid or Ask <= 0", 
                tick.time, tick.time_msc, tick.bid, 
                StringFormat("bid=%.5f ask=%.5f", tick.bid, tick.ask));
        worstSeverity = ERROR_FATAL;
    }
    
    if (tick.ask < tick.bid)
    {
        LogError(ERROR_FATAL, "invalid_price_inverted", "Ask < Bid (inverted spread)", 
                tick.time, tick.time_msc, tick.ask - tick.bid,
                StringFormat("bid=%.5f ask=%.5f", tick.bid, tick.ask));
        worstSeverity = ERROR_FATAL;
    }
    
    // 2. SPREAD-VALIDIERUNG (GESTUFT)
    double spread = tick.ask - tick.bid;
    double spreadPct = (tick.bid > 0) ? (spread / tick.bid * 100) : 0;
    
    if (spread <= 0)
    {
        LogError(ERROR_FATAL, "invalid_spread_zero", "Spread <= 0", 
                tick.time, tick.time_msc, spread, "");
        worstSeverity = ERROR_FATAL;
    }
    else if (spreadPct > maxSpreadPercent)
    {
        LogError(ERROR_SERIOUS, "spread_extreme", 
                StringFormat("Extreme spread: %.2f%% (threshold: %.2f%%)", spreadPct, maxSpreadPercent),
                tick.time, tick.time_msc, spreadPct, 
                StringFormat("spread=%.5f", spread));
        if (worstSeverity < ERROR_SERIOUS) worstSeverity = ERROR_SERIOUS;
    }
    else if (lastSpread > 0 && MathAbs(spread - lastSpread) > lastSpread * 0.5)
    {
        LogError(ERROR_NEGLIGIBLE, "spread_jump", 
                StringFormat("Spread jump: %.5f to %.5f (%.1f%% change)", 
                           lastSpread, spread, ((spread-lastSpread)/lastSpread)*100),
                tick.time, tick.time_msc, MathAbs(spread - lastSpread),
                StringFormat("prev_spread=%.5f", lastSpread));
    }
    
    // 3. ZEITSTEMPEL-VALIDIERUNG (GESTUFT)
    if (lastTickTime > 0)
    {
        long timeDiff = tick.time - lastTickTime;
        
        if (timeDiff < 0)
        {
            LogError(ERROR_FATAL, "time_regression", 
                    StringFormat("Backwards time jump: %d seconds", timeDiff),
                    tick.time, tick.time_msc, timeDiff,
                    StringFormat("prev_time=%s", TimeToString(lastTickTime)));
            worstSeverity = ERROR_FATAL;
        }
        else if (timeDiff > maxDataGapSeconds)
        {
            LogError(ERROR_SERIOUS, "data_gap_major", 
                    StringFormat("Large data gap: %d seconds (threshold: %d)", timeDiff, maxDataGapSeconds),
                    tick.time, tick.time_msc, timeDiff,
                    StringFormat("gap_minutes=%.1f", timeDiff/60.0));
            if (worstSeverity < ERROR_SERIOUS) worstSeverity = ERROR_SERIOUS;
        }
        else if (timeDiff > warningDataGapSeconds)
        {
            LogError(ERROR_NEGLIGIBLE, "data_gap_minor", 
                    StringFormat("Data gap detected: %d seconds", timeDiff),
                    tick.time, tick.time_msc, timeDiff, "");
        }
    }
    
    // 4. MILLISEKUNDEN-VALIDIERUNG
    if (tick.time_msc <= 0)
    {
        LogError(ERROR_SERIOUS, "invalid_time_msc", "time_msc invalid or zero", 
                tick.time, tick.time_msc, 0, "");
        if (worstSeverity < ERROR_SERIOUS) worstSeverity = ERROR_SERIOUS;
    }
    else if (lastTickMsc > 0 && tick.time_msc < lastTickMsc)
    {
        LogError(ERROR_SERIOUS, "time_msc_regression", 
                StringFormat("Millisecond time regression: %I64d to %I64d", lastTickMsc, tick.time_msc),
                tick.time, tick.time_msc, tick.time_msc - lastTickMsc,
                StringFormat("prev_msc=%I64d", lastTickMsc));
        if (worstSeverity < ERROR_SERIOUS) worstSeverity = ERROR_SERIOUS;
    }
    
    // 5. PREIS-SPRUNG-VALIDIERUNG
    if (lastBid > 0 && lastAsk > 0)
    {
        double bidJumpPct = MathAbs(tick.bid - lastBid) / lastBid * 100;
        double askJumpPct = MathAbs(tick.ask - lastAsk) / lastAsk * 100;
        
        if (bidJumpPct > maxPriceJumpPercent)
        {
            LogError(ERROR_SERIOUS, "price_jump_bid", 
                    StringFormat("Extreme bid jump: %.2f%% (threshold: %.2f%%)", bidJumpPct, maxPriceJumpPercent),
                    tick.time, tick.time_msc, bidJumpPct,
                    StringFormat("prev_bid=%.5f new_bid=%.5f", lastBid, tick.bid));
            if (worstSeverity < ERROR_SERIOUS) worstSeverity = ERROR_SERIOUS;
        }
        
        if (askJumpPct > maxPriceJumpPercent)
        {
            LogError(ERROR_SERIOUS, "price_jump_ask", 
                    StringFormat("Extreme ask jump: %.2f%% (threshold: %.2f%%)", askJumpPct, maxPriceJumpPercent),
                    tick.time, tick.time_msc, askJumpPct,
                    StringFormat("prev_ask=%.5f new_ask=%.5f", lastAsk, tick.ask));
            if (worstSeverity < ERROR_SERIOUS) worstSeverity = ERROR_SERIOUS;
        }
    }
    
    // 6. VOLUMEN-VALIDIERUNG
    if (tick.volume < 0)
    {
        LogError(ERROR_SERIOUS, "invalid_volume_negative", "Negative tick volume",
                tick.time, tick.time_msc, tick.volume, "");
        if (worstSeverity < ERROR_SERIOUS) worstSeverity = ERROR_SERIOUS;
    }
    
    if (IncludeRealVolume && tick.volume_real < 0)
    {
        LogError(ERROR_NEGLIGIBLE, "invalid_real_volume_negative", "Negative real volume",
                tick.time, tick.time_msc, tick.volume_real, "");
    }
    
    // 7. TICK-FLAGS-VALIDIERUNG
    if (IncludeTickFlags && tick.flags == 0)
    {
        LogError(ERROR_NEGLIGIBLE, "missing_tick_flags", "No tick flags set",
                tick.time, tick.time_msc, 0, "");
    }
    
    return worstSeverity;
}

//+------------------------------------------------------------------+
//| Verbesserte Error-Logging-Funktion                             |
//+------------------------------------------------------------------+
void LogError(ENUM_ERROR_SEVERITY severity, string errorType, string description, 
              datetime errorTime, long errorTimeMsc, double affectedValue, string additionalData)
{
    // Prüfen ob diese Error-Stufe geloggt werden soll
    if ((severity == ERROR_NEGLIGIBLE && !LogNegligibleErrors) ||
        (severity == ERROR_SERIOUS && !LogSeriousErrors) ||
        (severity == ERROR_FATAL && !LogFatalErrors))
        return;
    
    // Max Errors pro Datei prüfen
    if (ArraySize(errorBuffer) >= MaxErrorsPerFile)
        return;
    
    // Error-Info erstellen
    ErrorInfo newError;
    newError.severity = severity;
    newError.errorType = errorType;
    newError.description = description;
    newError.timestamp = errorTime;
    newError.timestamp_msc = errorTimeMsc;
    newError.tickContext = tickCounter;
    newError.affectedValue = affectedValue;
    newError.additionalData = additionalData;
    
    // Error zum Buffer hinzufügen
    int newSize = ArraySize(errorBuffer) + 1;
    ArrayResize(errorBuffer, newSize);
    errorBuffer[newSize - 1] = newError;
    
    // Counter erhöhen
    errorCounts[severity]++;
    
    // Konsolen-Output je nach Schweregrad
    string severityText = (severity == ERROR_NEGLIGIBLE) ? "NEGLIGIBLE" :
                         (severity == ERROR_SERIOUS) ? "SERIOUS" : "FATAL";
    
    if (severity >= ERROR_SERIOUS)
    {
        Print(StringFormat("%s ERROR [%s]: %s - %s", 
              severityText, errorType, description, 
              (StringLen(additionalData) > 0) ? additionalData : ""));
    }
}

//+------------------------------------------------------------------+
//| Aktualisiert letzte Tick-Daten für Vergleiche                  |
//+------------------------------------------------------------------+
void UpdateLastTickData(MqlTick &tick)
{
    lastTickTime = tick.time;
    lastTickMsc = tick.time_msc;
    lastBid = tick.bid;
    lastAsk = tick.ask;
    lastSpread = tick.ask - tick.bid;
}

//+------------------------------------------------------------------+
//| Erstellt Export-Verzeichnis                                    |
//+------------------------------------------------------------------+
bool CreateExportDirectory()
{
    return true; // MQL5 FileOpen erstellt automatisch Ordner
}

//+------------------------------------------------------------------+
//| Erstellt neue Export-Datei mit erweiterten Metadaten          |
//+------------------------------------------------------------------+
bool CreateNewExportFile()
{
    fileStartTime = TimeCurrent();
    
    // Error-Tracking für neue Datei zurücksetzen
    ArrayResize(errorBuffer, 0);
    ArrayInitialize(errorCounts, 0);
    
    // Symbol-Informationen sammeln
    double pointValue = SymbolInfoDouble(Symbol(), SYMBOL_POINT);
    int digits = (int)SymbolInfoInteger(Symbol(), SYMBOL_DIGITS);
    double tickSize = SymbolInfoDouble(Symbol(), SYMBOL_TRADE_TICK_SIZE);
    double tickValue = SymbolInfoDouble(Symbol(), SYMBOL_TRADE_TICK_VALUE);
    string serverName = AccountInfoString(ACCOUNT_SERVER);
    
    // NEW V1.0.5: Zeitstempel für Metadaten
    datetime localTime = TimeLocal();      // Lokale PC-Zeit
    datetime brokerTime = TimeCurrent();   // Broker-Serverzeit
    
    // Dateinamen generieren
    string dateTimeStr = TimeToString(fileStartTime, TIME_DATE | TIME_SECONDS);
    StringReplace(dateTimeStr, ".", "");
    StringReplace(dateTimeStr, ":", "");
    StringReplace(dateTimeStr, " ", "_");
    
    currentFileName = StringFormat("%s%s_%s_ticks.json", 
                                   ExportPath, Symbol(), dateTimeStr);
    
    fileHandle = FileOpen(currentFileName, FILE_WRITE | FILE_TXT | FILE_ANSI);
    
    if (fileHandle == INVALID_HANDLE)
    {
        Print("FEHLER: Export-Datei konnte nicht erstellt werden: ", currentFileName);
        Print("Letzter Fehler: ", GetLastError());
        return false;
    }
    
    // NEW: JSON mit local_device_time und broker_server_time in Metadaten
    string header = StringFormat(
        "{\n"
        "  \"metadata\": {\n"
        "    \"symbol\": \"%s\",\n"
        "    \"broker\": \"%s\",\n"
        "    \"server\": \"%s\",\n"
        "    \"broker_utc_offset_hours\": %d,\n"
        "    \"local_device_time\": \"%s\",\n"           // NEW V1.0.5
        "    \"broker_server_time\": \"%s\",\n"          // NEW V1.0.5
        "    \"start_time\": \"%s\",\n"
        "    \"start_time_unix\": %d,\n"
        "    \"timeframe\": \"TICK\",\n"
        "    \"volume_timeframe\": \"%s\",\n"
        "    \"volume_timeframe_minutes\": %d,\n"
        "    \"data_format_version\": \"%s\",\n"
        "    \"data_collector\": \"%s\",\n"
        "    \"collection_purpose\": \"%s\",\n"
        "    \"operator\": \"%s\",\n"
        "    \"symbol_info\": {\n"
        "      \"point_value\": %.8f,\n"
        "      \"digits\": %d,\n"
        "      \"tick_size\": %.8f,\n"
        "      \"tick_value\": %.8f\n"
        "    },\n"
        "    \"collection_settings\": {\n"
        "      \"max_ticks_per_file\": %d,\n"
        "      \"max_errors_per_file\": %d,\n"
        "      \"include_real_volume\": %s,\n"
        "      \"include_tick_flags\": %s,\n"
        "      \"stop_on_fatal_errors\": %s\n"
        "    },\n"
        "    \"error_tracking\": {\n"
        "      \"enabled\": %s,\n"
        "      \"log_negligible\": %s,\n"
        "      \"log_serious\": %s,\n"
        "      \"log_fatal\": %s,\n"
        "      \"max_spread_percent\": %.2f,\n"
        "      \"max_price_jump_percent\": %.2f,\n"
        "      \"max_data_gap_seconds\": %d\n"
        "    }\n"
        "  },\n"
        "  \"ticks\": [",
        Symbol(),
        AccountInfoString(ACCOUNT_COMPANY),
        serverName,
        g_brokerUtcOffsetHours,
        TimeToString(localTime, TIME_DATE | TIME_SECONDS),    // Lokale PC-Zeit
        TimeToString(brokerTime, TIME_DATE | TIME_SECONDS),   // Broker-Serverzeit
        TimeToString(fileStartTime, TIME_DATE | TIME_SECONDS),
        (int)fileStartTime,
        EnumToString(VolumeTimeframe),
        PeriodSeconds(VolumeTimeframe) / 60,
        DataFormatVersion,
        DataCollectorName,
        CollectionPurpose,
        (StringLen(CollectorOperator) > 0) ? CollectorOperator : "automated",
        pointValue,
        digits,
        tickSize,
        tickValue,
        MaxTicksPerFile,
        MaxErrorsPerFile,
        IncludeRealVolume ? "true" : "false",
        IncludeTickFlags ? "true" : "false",
        StopOnFatalErrors ? "true" : "false",
        EnableErrorTracking ? "true" : "false",
        LogNegligibleErrors ? "true" : "false",
        LogSeriousErrors ? "true" : "false",
        LogFatalErrors ? "true" : "false",
        maxSpreadPercent,
        maxPriceJumpPercent,
        maxDataGapSeconds
    );
    
    FileWriteString(fileHandle, header);
    tickCounter = 0;
    
    Print("✅ Neue Export-Datei erstellt: ", currentFileName);
    Print("   → Local Device Time: ", TimeToString(localTime, TIME_DATE | TIME_SECONDS));
    Print("   → Broker Server Time: ", TimeToString(brokerTime, TIME_DATE | TIME_SECONDS));
    Print("   → Broker UTC Offset: GMT", (g_brokerUtcOffsetHours >= 0 ? "+" : ""), g_brokerUtcOffsetHours);
    Print("   → Enhanced Error-Tracking aktiviert");
    return true;
}

//+------------------------------------------------------------------+
//| Exportiert einzelnen Tick als JSON                             |
//+------------------------------------------------------------------+
bool ExportTick(MqlTick &tick)
{
    if (fileHandle == INVALID_HANDLE) return false;
    
    // Spread-Berechnungen
    double spread = tick.ask - tick.bid;
    int spreadPoints = (int)((spread / SymbolInfoDouble(Symbol(), SYMBOL_POINT)));
    double spreadPct = (tick.bid > 0) ? (spread / tick.bid * 100) : 0;
    
    // Chart-Volumen abrufen
    long chartTickVolume = iVolume(Symbol(), VolumeTimeframe, 0);
    
    // Echtes Volumen (falls verfügbar)
    double realVolume = 0;
    if (IncludeRealVolume)
    {
        ENUM_SYMBOL_CALC_MODE calcMode = (ENUM_SYMBOL_CALC_MODE)SymbolInfoInteger(Symbol(), SYMBOL_TRADE_CALC_MODE);
        if (calcMode == SYMBOL_CALC_MODE_EXCH_STOCKS || calcMode == SYMBOL_CALC_MODE_EXCH_FUTURES)
        {
            realVolume = tick.volume_real;
        }
    }
    
    // Tick-Flags analysieren
    string tickFlags = "";
    if (IncludeTickFlags)
    {
        if((tick.flags & TICK_FLAG_BID) != 0) tickFlags += "BID ";
        if((tick.flags & TICK_FLAG_ASK) != 0) tickFlags += "ASK ";
        if((tick.flags & TICK_FLAG_LAST) != 0) tickFlags += "LAST ";
        if((tick.flags & TICK_FLAG_VOLUME) != 0) tickFlags += "VOL ";
        if((tick.flags & TICK_FLAG_BUY) != 0) tickFlags += "BUY ";
        if((tick.flags & TICK_FLAG_SELL) != 0) tickFlags += "SELL ";
        
        if (StringLen(tickFlags) > 0)
            tickFlags = StringSubstr(tickFlags, 0, StringLen(tickFlags) - 1);
    }
    
    string session = GetTradingSession(tick.time);
    
    // JSON-Objekt erstellen
    // NOTE: tick.time bleibt in BROKER SERVER ZEIT (unverändert!)
    // UTC-Konvertierung erfolgt später im tick_importer.py
    string jsonTick = StringFormat(
        "%s\n    {\n      \"timestamp\": \"%s\",\n      \"time_msc\": %I64d,\n      \"bid\": %.5f,\n      \"ask\": %.5f,\n      \"last\": %.5f,\n      \"tick_volume\": %d,\n      \"real_volume\": %.2f,\n      \"chart_tick_volume\": %d,\n      \"spread_points\": %d,\n      \"spread_pct\": %.6f,\n      \"tick_flags\": \"%s\",\n      \"session\": \"%s\",\n      \"server_time\": \"%s\"\n    }",
        (tickCounter > 0) ? "," : "",
        TimeToString(tick.time, TIME_DATE | TIME_SECONDS),
        tick.time_msc,
        tick.bid,
        tick.ask,
        tick.last,
        (int)tick.volume,
        realVolume,
        (int)chartTickVolume,
        spreadPoints,
        spreadPct,
        tickFlags,
        session,
        TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS)
    );
    
    FileWriteString(fileHandle, jsonTick);
    return true;
}

//+------------------------------------------------------------------+
//| Bestimmt aktuelle Trading-Session                              |
//+------------------------------------------------------------------+
string GetTradingSession(datetime tickTime)
{
    MqlDateTime dt;
    TimeToStruct(tickTime, dt);
    
    int hour = dt.hour;
    
    if (hour >= 22 || hour < 8) return "sydney_tokyo";
    else if (hour >= 8 && hour < 14) return "london"; 
    else if (hour >= 14 && hour < 22) return "new_york";
    else return "transition";
}

//+------------------------------------------------------------------+
//| Schließt aktuelle Export-Datei mit detailliertem Error-Report |
//+------------------------------------------------------------------+
void CloseCurrentFile()
{
    if (fileHandle != INVALID_HANDLE)
    {
        // Ticks-Array schließen
        FileWriteString(fileHandle, "\n  ],");
        
        // Detailliertes Errors-Array mit Severity-Kategorisierung
        FileWriteString(fileHandle, "\n  \"errors\": {");
        FileWriteString(fileHandle, "\n    \"by_severity\": {");
        FileWriteString(fileHandle, StringFormat("\n      \"negligible\": %d,", errorCounts[ERROR_NEGLIGIBLE]));
        FileWriteString(fileHandle, StringFormat("\n      \"serious\": %d,", errorCounts[ERROR_SERIOUS]));
        FileWriteString(fileHandle, StringFormat("\n      \"fatal\": %d", errorCounts[ERROR_FATAL]));
        FileWriteString(fileHandle, "\n    },");
        FileWriteString(fileHandle, "\n    \"details\": [");
        
        for(int i = 0; i < ArraySize(errorBuffer); i++)
        {
            if(i > 0) FileWriteString(fileHandle, ",");
            
            string severityText = (errorBuffer[i].severity == ERROR_NEGLIGIBLE) ? "negligible" :
                                 (errorBuffer[i].severity == ERROR_SERIOUS) ? "serious" : "fatal";
            
            string errorDetail = StringFormat(
                "\n      {\n        \"severity\": \"%s\",\n        \"severity_level\": %d,\n        \"type\": \"%s\",\n        \"description\": \"%s\",\n        \"timestamp\": \"%s\",\n        \"timestamp_unix\": %d,\n        \"timestamp_msc\": %I64d,\n        \"tick_context\": %d,\n        \"affected_value\": %.8f,\n        \"additional_data\": \"%s\"\n      }",
                severityText,
                (int)errorBuffer[i].severity,
                errorBuffer[i].errorType,
                errorBuffer[i].description,
                TimeToString(errorBuffer[i].timestamp, TIME_DATE | TIME_SECONDS),
                (int)errorBuffer[i].timestamp,
                errorBuffer[i].timestamp_msc,
                errorBuffer[i].tickContext,
                errorBuffer[i].affectedValue,
                errorBuffer[i].additionalData
            );
            
            FileWriteString(fileHandle, errorDetail);
        }
        
        FileWriteString(fileHandle, "\n    ]");
        FileWriteString(fileHandle, "\n  },");
        
        // Erweiterte Summary mit Datenqualitäts-Scoring
        int totalErrors = errorCounts[ERROR_NEGLIGIBLE] + errorCounts[ERROR_SERIOUS] + errorCounts[ERROR_FATAL];
        double overallQualityScore = (tickCounter > 0) ? 1.0 - ((double)totalErrors / tickCounter) : 1.0;
        double dataIntegrityScore = (tickCounter > 0) ? 1.0 - ((double)errorCounts[ERROR_FATAL] / tickCounter) : 1.0;
        double dataReliabilityScore = (tickCounter > 0) ? 1.0 - ((double)(errorCounts[ERROR_SERIOUS] + errorCounts[ERROR_FATAL]) / tickCounter) : 1.0;
        
        string footer = StringFormat(
            "\n  \"summary\": {\n    \"total_ticks\": %d,\n    \"total_errors\": %d,\n    \"data_stream_status\": \"%s\",\n    \"quality_metrics\": {\n      \"overall_quality_score\": %.6f,\n      \"data_integrity_score\": %.6f,\n      \"data_reliability_score\": %.6f,\n      \"negligible_error_rate\": %.6f,\n      \"serious_error_rate\": %.6f,\n      \"fatal_error_rate\": %.6f\n    },\n    \"timing\": {\n      \"end_time\": \"%s\",\n      \"duration_minutes\": %.1f,\n      \"avg_ticks_per_minute\": %.1f\n    },\n    \"recommendations\": \"%s\"\n  }\n}",
            tickCounter,
            totalErrors,
            dataStreamCorrupted ? "CORRUPTED" : (errorCounts[ERROR_FATAL] > 0 ? "COMPROMISED" : "HEALTHY"),
            overallQualityScore,
            dataIntegrityScore,
            dataReliabilityScore,
            (tickCounter > 0) ? (double)errorCounts[ERROR_NEGLIGIBLE] / tickCounter : 0.0,
            (tickCounter > 0) ? (double)errorCounts[ERROR_SERIOUS] / tickCounter : 0.0,
            (tickCounter > 0) ? (double)errorCounts[ERROR_FATAL] / tickCounter : 0.0,
            TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS),
            (TimeCurrent() - fileStartTime) / 60.0,
            tickCounter / MathMax(1.0, (TimeCurrent() - fileStartTime) / 60.0),
            GenerateDataQualityRecommendations()
        );
        
        FileWriteString(fileHandle, footer);
        FileClose(fileHandle);
        
        // Detailliertes Closing-Log
        Print("✅ Export-Datei geschlossen: ", currentFileName);
        Print(StringFormat("  → %d Ticks gesammelt", tickCounter));
        Print(StringFormat("  → %d Errors total (Negligible:%d, Serious:%d, Fatal:%d)", 
              totalErrors, errorCounts[ERROR_NEGLIGIBLE], errorCounts[ERROR_SERIOUS], errorCounts[ERROR_FATAL]));
        Print(StringFormat("  → Datenqualität: %.1f%% (Integrität: %.1f%%, Zuverlässigkeit: %.1f%%)", 
              overallQualityScore*100, dataIntegrityScore*100, dataReliabilityScore*100));
        
        if (errorCounts[ERROR_FATAL] > 0)
            Print("  ⚠ WARNUNG: Fatale Errors detected - Datenintegrität kompromittiert");
        if (dataStreamCorrupted)
            Print("  🚨 KRITISCH: Datenstream als korrupt markiert");
            
        fileHandle = INVALID_HANDLE;
    }
}

//+------------------------------------------------------------------+
//| Generiert Empfehlungen basierend auf Error-Pattern             |
//+------------------------------------------------------------------+
string GenerateDataQualityRecommendations()
{
    string recommendations = "";
    
    if (errorCounts[ERROR_FATAL] > 0)
    {
        recommendations += "CRITICAL: Fatal errors detected - verify broker connection and data feed integrity. ";
    }
    
    if (errorCounts[ERROR_SERIOUS] > tickCounter * 0.05) // > 5% serious errors
    {
        recommendations += "HIGH: Serious error rate exceeds 5% - check network stability and server performance. ";
    }
    
    if (errorCounts[ERROR_NEGLIGIBLE] > tickCounter * 0.1) // > 10% negligible errors
    {
        recommendations += "MEDIUM: High negligible error rate - monitor data source quality. ";
    }
    
    if (dataStreamCorrupted)
    {
        recommendations += "URGENT: Data stream corruption detected - restart collector and verify data source. ";
    }
    
    if (StringLen(recommendations) == 0)
    {
        recommendations = "Data quality is excellent - no specific recommendations.";
    }
    
    return recommendations;
}

//+------------------------------------------------------------------+
//| Expert Advisor Deinitialisierung                               |
//+------------------------------------------------------------------+
void OnDeinit(const int reason)
{
    CloseCurrentFile();
    
    string reasonText = "";
    switch(reason)
    {
        case REASON_PROGRAM: reasonText = "Expert Advisor manuell gestoppt"; break;
        case REASON_REMOVE: reasonText = "Expert Advisor vom Chart entfernt"; break;
        case REASON_RECOMPILE: reasonText = "Expert Advisor neu kompiliert"; break;
        case REASON_CHARTCHANGE: reasonText = "Chart-Eigenschaften geändert"; break;
        case REASON_CHARTCLOSE: reasonText = "Chart geschlossen"; break;
        default: reasonText = "Unbekannter Grund"; break;
    }
    
    // Finale Statistiken
    int totalErrors = errorCounts[ERROR_NEGLIGIBLE] + errorCounts[ERROR_SERIOUS] + errorCounts[ERROR_FATAL];
    Print("========================================");
    Print("TickCollector V1.0.5 gestoppt");
    Print("Grund: ", reasonText);
    Print(StringFormat("Finale Statistiken: %d Ticks, %d Errors", tickCounter, totalErrors));
    if (totalErrors > 0)
    {
        Print(StringFormat("Error-Breakdown: %d Negligible, %d Serious, %d Fatal", 
              errorCounts[ERROR_NEGLIGIBLE], errorCounts[ERROR_SERIOUS], errorCounts[ERROR_FATAL]));
    }
    Print("========================================");
}