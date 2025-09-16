//+------------------------------------------------------------------+
//| FinexTestingIDE Tick Data Collector                             |
//| Sammelt Live-Tick-Daten für Backtesting                        |
//+------------------------------------------------------------------+
#property copyright "FinexTestingIDE"
#property version   "1.00"
#property strict

// Input-Parameter
input string ExportPath = "C:\\FinexData\\";        // Export-Ordner
input bool CollectTicks = true;                     // Sammlung ein/aus
input int MaxTicksPerFile = 50000;                  // Ticks pro Datei (größere Files für längere Runs)
input bool IncludeRealVolume = true;                // Echtes Volumen sammeln (wenn verfügbar)

// Globale Variablen
int fileHandle = INVALID_HANDLE;
string currentFileName = "";
int tickCounter = 0;
datetime fileStartTime;

//+------------------------------------------------------------------+
//| Expert Advisor Initialisierung                                  |
//+------------------------------------------------------------------+
int OnInit()
{
    // Prüfen ob Export-Ordner existiert
    if(!CreateExportDirectory())
    {
        Alert("FEHLER: Export-Ordner konnte nicht erstellt werden: ", ExportPath);
        return INIT_FAILED;
    }
    
    // Erste Export-Datei erstellen
    if(!CreateNewExportFile())
    {
        Alert("FEHLER: Erste Export-Datei konnte nicht erstellt werden");
        return INIT_FAILED;
    }
    
    Print("✓ TickCollector erfolgreich gestartet für ", Symbol());
    Print("✓ Export-Pfad: ", ExportPath);
    Print("✓ Max Ticks pro Datei: ", MaxTicksPerFile);
    
    return INIT_SUCCEEDED;
}

//+------------------------------------------------------------------+
//| Tick-Event Handler                                               |
//+------------------------------------------------------------------+
void OnTick()
{
    if (!CollectTicks) return;
    
    // Aktuellen Tick abrufen
    MqlTick tick;
    if (!SymbolInfoTick(Symbol(), tick))
    {
        Print("WARNUNG: Tick-Daten für ", Symbol(), " nicht verfügbar");
        return;
    }
    
    // Tick exportieren
    if (ExportTick(tick))
    {
        tickCounter++;
        
        // Datei-Rotation bei Erreichen der maximalen Tick-Anzahl
        if (tickCounter >= MaxTicksPerFile)
        {
            CloseCurrentFile();
            CreateNewExportFile();
        }
    }
}

//+------------------------------------------------------------------+
//| Erstellt Export-Verzeichnis falls nicht vorhanden              |
//+------------------------------------------------------------------+
bool CreateExportDirectory()
{
    // Verzeichnis erstellen (MQL5 macht das automatisch falls nicht vorhanden)
    return true; // MQL5 FileOpen erstellt automatisch Ordner
}

//+------------------------------------------------------------------+
//| Erstellt neue Export-Datei mit JSON-Header                     |
//+------------------------------------------------------------------+
bool CreateNewExportFile()
{
    fileStartTime = TimeCurrent();
    
    // Dateinamen generieren: SYMBOL_YYYYMMDD_HHMMSS_ticks.json
    string dateTimeStr = TimeToString(fileStartTime, TIME_DATE | TIME_SECONDS);
    StringReplace(dateTimeStr, ".", "");
    StringReplace(dateTimeStr, ":", "");
    StringReplace(dateTimeStr, " ", "_");
    
    currentFileName = StringFormat("%s%s_%s_ticks.json", 
                                   ExportPath, Symbol(), dateTimeStr);
    
    // Datei öffnen
    fileHandle = FileOpen(currentFileName, FILE_WRITE | FILE_TXT | FILE_ANSI);
    
    if (fileHandle == INVALID_HANDLE)
    {
        Print("FEHLER: Export-Datei konnte nicht erstellt werden: ", currentFileName);
        Print("Letzter Fehler: ", GetLastError());
        return false;
    }
    
    // JSON-Array beginnen + Metadaten
    string header = StringFormat("{\n  \"metadata\": {\n    \"symbol\": \"%s\",\n    \"broker\": \"%s\",\n    \"start_time\": \"%s\",\n    \"timeframe\": \"TICK\",\n    \"collector_version\": \"1.00\"\n  },\n  \"ticks\": [",
                                Symbol(),
                                AccountInfoString(ACCOUNT_COMPANY),
                                TimeToString(fileStartTime, TIME_DATE | TIME_SECONDS));
    
    FileWriteString(fileHandle, header);
    tickCounter = 0;
    
    Print("✓ Neue Export-Datei erstellt: ", currentFileName);
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
    
    // Echtes Volumen (falls verfügbar)
    long realVolume = 0;
    bool hasRealVolume = false;
    
    if (IncludeRealVolume)
    {
        // Prüfen ob Symbol echtes Volumen unterstützt
        ENUM_SYMBOL_CALC_MODE calcMode = (ENUM_SYMBOL_CALC_MODE)SymbolInfoInteger(Symbol(), SYMBOL_TRADE_CALC_MODE);
        if (calcMode == SYMBOL_CALC_MODE_EXCH_STOCKS || calcMode == SYMBOL_CALC_MODE_EXCH_FUTURES)
        {
            realVolume = tick.real_volume;
            hasRealVolume = true;
        }
    }
    
    // Session-Info bestimmen
    string session = GetTradingSession(tick.time);
    
    // JSON-Objekt erstellen
    string jsonTick = StringFormat(
        "%s\n    {\n      \"timestamp\": \"%s\",\n      \"bid\": %.5f,\n      \"ask\": %.5f,\n      \"last\": %.5f,\n      \"tick_volume\": %d,\n      \"real_volume\": %d,\n      \"spread_points\": %d,\n      \"spread_pct\": %.6f,\n      \"session\": \"%s\",\n      \"server_time\": \"%s\"\n    }",
        (tickCounter > 0) ? "," : "",  // Komma außer beim ersten Tick
        TimeToString(tick.time, TIME_DATE | TIME_SECONDS | TIME_MINUTES),
        tick.bid,
        tick.ask,
        tick.last,
        (int)tick.volume,
        (int)realVolume,
        spreadPoints,
        spreadPct,
        session,
        TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS | TIME_MINUTES)
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
    
    // Vereinfachte Session-Logik (UTC-basiert)
    if (hour >= 22 || hour < 8) return "sydney_tokyo";
    else if (hour >= 8 && hour < 14) return "london"; 
    else if (hour >= 14 && hour < 22) return "new_york";
    else return "transition";
}

//+------------------------------------------------------------------+
//| Schließt aktuelle Export-Datei ordnungsgemäß                  |
//+------------------------------------------------------------------+
void CloseCurrentFile()
{
    if (fileHandle != INVALID_HANDLE)
    {
        // JSON-Array und Objekt schließen
        string footer = StringFormat("\n  ],\n  \"summary\": {\n    \"total_ticks\": %d,\n    \"end_time\": \"%s\",\n    \"duration_minutes\": %.1f\n  }\n}",
                                    tickCounter,
                                    TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS),
                                    (TimeCurrent() - fileStartTime) / 60.0);
        
        FileWriteString(fileHandle, footer);
        FileClose(fileHandle);
        
        Print("✓ Export-Datei geschlossen: ", currentFileName, " (", tickCounter, " Ticks)");
        fileHandle = INVALID_HANDLE;
    }
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
    
    Print("TickCollector gestoppt - Grund: ", reasonText);
}
