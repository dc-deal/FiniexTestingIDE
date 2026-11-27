//+------------------------------------------------------------------+
//| FiniexTestingIDE Keepalive                                       |
//| Tells an outside monitor that this terminal is alive             |
//|                                                                  |
//| Docs:                                                            |
//| - docs/data_pipeline/tick_collector_guide.md  (The outside check)|
//+------------------------------------------------------------------+
#property copyright "FiniexTestingIDE"
#property strict

// An expert of its own, on a chart of its own, one per terminal. WebRequest blocks its thread, and
// the collector reads only the latest tick per OnTick: a ping inside the collector would lose the
// ticks that arrive during it. Each expert has its own thread, so a slow ping here costs them nothing.
//
// What it vouches for: the machine, the terminal, while a session is open the broker connection, and
// that every TickCollector named in Collectors runs: each sets its alive stamp, a terminal global
// variable, every second, and one that is missing or stale withholds the ping.
// What it does NOT vouch for: that ticks arrive. An alive stamp says the collector runs, not that its
// symbol is quoted - the market may be closed.

#define KEEPALIVE_VERSION "1.1.0"
// The TickCollector's alive stamp: this prefix plus the symbol. The collector spells the same prefix;
// the two must agree.
#define ALIVE_STAMP_PREFIX "FiniexTickCollector."

// The check's ping address. A credential: whoever holds it can report this terminal healthy. It stays
// in this chart's settings on this machine, and it is never printed.
input string PingUrl = "";
input int PingIntervalSeconds = 60;
input int PingTimeoutMilliseconds = 10000;
// The symbols whose TickCollector must run, comma-separated and spelled as their charts name them.
// Empty: this terminal collects nothing, and only the terminal is vouched for.
input string Collectors = "";
// How old an alive stamp may be before its collector counts as down. It is set every second.
input int CollectorStaleSeconds = 180;

// What the last timer event ended in. A line is logged only when this, or its reason, changes.
enum ENUM_KEEPALIVE_STATE
{
    KEEPALIVE_STARTING,
    KEEPALIVE_SENDING,
    KEEPALIVE_WITHHELD,
    KEEPALIVE_COLLECTOR_DOWN,
    KEEPALIVE_FAILING
};

ENUM_KEEPALIVE_STATE g_state = KEEPALIVE_STARTING;
string g_why = "";
long g_beatsSent = 0;
long g_beatsWithheld = 0;
long g_beatsFailed = 0;
datetime g_lastSent = 0;
string g_collectors[];
string g_collectorsDown = "";

int OnInit()
{
    if (StringFind(PingUrl, "https://") != 0)
    {
        Print("Keepalive: PingUrl must be the check's https address. Not started.");
        return INIT_PARAMETERS_INCORRECT;
    }
    if (!ReadCollectors())
    {
        Print("Keepalive: Collectors names an empty symbol - check the commas. Not started.");
        return INIT_PARAMETERS_INCORRECT;
    }
    // The first ping waits one interval: the terminal may still be connecting after its start
    if (!EventSetTimer(PingIntervalSeconds))
    {
        Print("Keepalive: the timer could not be set, error ", GetLastError(), ". Not started.");
        return INIT_FAILED;
    }
    Print("Keepalive ", KEEPALIVE_VERSION, " started, every ", PingIntervalSeconds, " s");
    if (ArraySize(g_collectors) == 0)
        Print("Keepalive: no collectors named - it vouches for the terminal only");
    else
        Print("Keepalive: watches the TickCollector of ", Collectors);
    ShowStatus();
    return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
    EventKillTimer();
    Comment("");
}

// Declared so the terminal treats this as an expert. Ticks mean nothing here: the market may be closed.
void OnTick()
{
}

void OnTimer()
{
    g_collectorsDown = CollectorsDown();
    if (g_collectorsDown != "")
    {
        g_beatsWithheld++;
        ChangeState(KEEPALIVE_COLLECTOR_DOWN, "TickCollector not running: " + g_collectorsDown);
        ShowStatus();
        return;
    }

    if (!MayVouch())
    {
        g_beatsWithheld++;
        ChangeState(KEEPALIVE_WITHHELD, "the broker connection is down during an open session");
        ShowStatus();
        return;
    }

    int answer = SendPing();
    if (answer == 200)
    {
        g_beatsSent++;
        g_lastSent = TimeGMT();
        ChangeState(KEEPALIVE_SENDING, "pings are going out");
    }
    else
    {
        g_beatsFailed++;
        ChangeState(KEEPALIVE_FAILING, FailureText(answer));
    }
    ShowStatus();
}

//+------------------------------------------------------------------+
//| Read the Collectors input into g_collectors.                     |
//|                                                                  |
//| Returns: false when it names an empty symbol ("A,,B", "A,")      |
//+------------------------------------------------------------------+
bool ReadCollectors()
{
    ArrayResize(g_collectors, 0);
    string names = Collectors;
    StringTrimLeft(names);
    StringTrimRight(names);
    if (names == "")
        return true;
    string parts[];
    int count = StringSplit(names, ',', parts);
    ArrayResize(g_collectors, count);
    for (int i = 0; i < count; i++)
    {
        string symbol = parts[i];
        StringTrimLeft(symbol);
        StringTrimRight(symbol);
        if (symbol == "")
            return false;
        g_collectors[i] = symbol;
    }
    return true;
}

//+------------------------------------------------------------------+
//| The named collectors whose alive stamp is missing or older than  |
//| CollectorStaleSeconds. The stamp holds the computer's UTC        |
//| time, so both experts read one clock; the server time would      |
//| stand still over a weekend.                                      |
//|                                                                  |
//| Returns: their symbols, comma-separated; empty when all run      |
//+------------------------------------------------------------------+
string CollectorsDown()
{
    string down = "";
    double now = (double)TimeGMT();
    for (int i = 0; i < ArraySize(g_collectors); i++)
    {
        string name = ALIVE_STAMP_PREFIX + g_collectors[i];
        if (GlobalVariableCheck(name) && now - GlobalVariableGet(name) <= CollectorStaleSeconds)
            continue;
        down += (down == "" ? "" : ", ") + g_collectors[i];
    }
    return down;
}

//+------------------------------------------------------------------+
//| Whether this terminal may report itself healthy.                 |
//| A connection lost outside a session is no fault: brokers run     |
//| their maintenance when the market is closed.                     |
//|                                                                  |
//| Returns: false only while disconnected during an open session    |
//+------------------------------------------------------------------+
bool MayVouch()
{
    if (TerminalInfoInteger(TERMINAL_CONNECTED))
        return true;
    return !SessionIsOpen();
}

//+------------------------------------------------------------------+
//| Whether this chart's symbol has a trading session open now.      |
//|                                                                  |
//| Returns: true when the broker's session table says so            |
//+------------------------------------------------------------------+
bool SessionIsOpen()
{
    MqlDateTime now;
    TimeToStruct(TimeTradeServer(), now);
    ENUM_DAY_OF_WEEK day = (ENUM_DAY_OF_WEEK)now.day_of_week;
    int secondsToday = now.hour * 3600 + now.min * 60 + now.sec;

    datetime from;
    datetime to;
    for (uint session = 0; SymbolInfoSessionTrade(Symbol(), day, session, from, to); session++)
    {
        if (secondsToday >= (int)from && secondsToday < (int)to)
            return true;
    }
    return false;
}

//+------------------------------------------------------------------+
//| One ping to the check.                                           |
//|                                                                  |
//| Returns: the HTTP status, or -1 when the request did not complete|
//+------------------------------------------------------------------+
int SendPing()
{
    char requestBody[];
    char responseBody[];
    string responseHeaders;
    ResetLastError();
    return WebRequest("GET", PingUrl, "", PingTimeoutMilliseconds, requestBody, responseBody,
                      responseHeaders);
}

//+------------------------------------------------------------------+
//| What a failed ping means, for the log. Never names the address.  |
//|                                                                  |
//| Args:                                                            |
//|   answer: What SendPing returned                                 |
//|                                                                  |
//| Returns: The sentence                                            |
//+------------------------------------------------------------------+
string FailureText(const int answer)
{
    if (answer != -1)
        return StringFormat("the check answered HTTP %d", answer);
    int error = GetLastError();
    // 4014: the terminal refuses the call until the address is on its list
    if (error == 4014)
        return "the address is not allowed: add https://hc-ping.com under Tools > Options > Expert Advisors";
    return StringFormat("the request did not complete, error %d", error);
}

//+------------------------------------------------------------------+
//| Log a line when the state or its reason changes, and only then:  |
//| a second collector going down while one is down is news too.     |
//|                                                                  |
//| Args:                                                            |
//|   state: The state the timer event ended in                      |
//|   why: The sentence the log line carries                         |
//+------------------------------------------------------------------+
void ChangeState(const ENUM_KEEPALIVE_STATE state, const string why)
{
    if (state == g_state && why == g_why)
        return;
    g_state = state;
    g_why = why;
    Print("Keepalive: ", why);
}

//+------------------------------------------------------------------+
//| The counters, on the chart: they tell an unhealthy terminal      |
//| (withheld) from a broken path to the check (failed), and name    |
//| the collectors that are down.                                    |
//+------------------------------------------------------------------+
void ShowStatus()
{
    string last = (g_lastSent > 0 ? TimeToString(g_lastSent, TIME_DATE | TIME_SECONDS) + " UTC" : "none yet");
    string watched = (ArraySize(g_collectors) == 0 ? "none named (terminal only)"
                      : StringFormat("%d watched", ArraySize(g_collectors)));
    string down = (g_collectorsDown == "" ? "" : "\nnot running: " + g_collectorsDown);
    Comment(StringFormat("Keepalive %s\nsent %I64d, withheld %I64d, failed %I64d\nlast ping: %s\ncollectors: %s%s",
                         KEEPALIVE_VERSION, g_beatsSent, g_beatsWithheld, g_beatsFailed, last, watched, down));
}
