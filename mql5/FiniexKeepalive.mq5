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
// What it vouches for: the machine, the terminal and, while a session is open, the broker connection.
// What it does NOT vouch for: that a TickCollector still runs on its chart. It reads nothing of theirs.

#define KEEPALIVE_VERSION "1.0.0"

// The check's ping address. A credential: whoever holds it can report this terminal healthy. It stays
// in this chart's settings on this machine, and it is never printed.
input string PingUrl = "";
input int PingIntervalSeconds = 60;
input int PingTimeoutMilliseconds = 10000;

// What the last timer event ended in. A line is logged only when this changes.
enum ENUM_KEEPALIVE_STATE
{
    KEEPALIVE_STARTING,
    KEEPALIVE_SENDING,
    KEEPALIVE_WITHHELD,
    KEEPALIVE_FAILING
};

ENUM_KEEPALIVE_STATE g_state = KEEPALIVE_STARTING;
long g_beatsSent = 0;
long g_beatsWithheld = 0;
long g_beatsFailed = 0;
datetime g_lastSent = 0;

int OnInit()
{
    if (StringFind(PingUrl, "https://") != 0)
    {
        Print("Keepalive: PingUrl must be the check's https address. Not started.");
        return INIT_PARAMETERS_INCORRECT;
    }
    // The first ping waits one interval: the terminal may still be connecting after its start
    if (!EventSetTimer(PingIntervalSeconds))
    {
        Print("Keepalive: the timer could not be set, error ", GetLastError(), ". Not started.");
        return INIT_FAILED;
    }
    Print("Keepalive ", KEEPALIVE_VERSION, " started, every ", PingIntervalSeconds, " s");
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
//| Log a line when the state changes, and only then.                |
//|                                                                  |
//| Args:                                                            |
//|   state: The state the timer event ended in                      |
//|   why: The sentence the log line carries                         |
//+------------------------------------------------------------------+
void ChangeState(const ENUM_KEEPALIVE_STATE state, const string why)
{
    if (state == g_state)
        return;
    g_state = state;
    Print("Keepalive: ", why);
}

//+------------------------------------------------------------------+
//| The counters, on the chart: they tell an unhealthy terminal      |
//| (withheld) from a broken path to the check (failed).             |
//+------------------------------------------------------------------+
void ShowStatus()
{
    string last = (g_lastSent > 0 ? TimeToString(g_lastSent, TIME_DATE | TIME_SECONDS) + " UTC" : "none yet");
    Comment(StringFormat("Keepalive %s\nsent %I64d, withheld %I64d, failed %I64d\nlast ping: %s",
                         KEEPALIVE_VERSION, g_beatsSent, g_beatsWithheld, g_beatsFailed, last));
}
