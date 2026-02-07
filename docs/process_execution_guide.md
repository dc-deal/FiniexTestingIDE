# Process Execution & Subprocess Architecture
**FiniexTestingIDE - Phase 2 Execution System**

---

## Overview

After data preparation completes, Phase 2 executes validated scenarios using Python's ProcessPoolExecutor or ThreadPoolExecutor. Each scenario runs in isolated execution context with complete object recreation in subprocess.

**Key Principle:** True parallelism without shared state contamination.

**Execution Modes:**
- **Sequential:** One scenario at a time (debugging, small batches)
- **Parallel ThreadPool:** GIL-limited concurrency (debugger-friendly)
- **Parallel ProcessPool:** True parallelism (production performance)

---

## Phase 2: Execution Coordination

### ExecutionCoordinator Responsibilities

**Located:** `python/framework/batch/execution_coordinator.py`

**Core Functions:**
1. Orchestrate sequential or parallel execution
2. Auto-detect debugger and switch execution mode
3. Handle validation skip logic
4. Collect and return ProcessResult objects
5. Manage executor lifecycle (startup + shutdown)

### Execution Flow

```
BatchOrchestrator (Main Process)
    ↓
ExecutionCoordinator.execute_sequential() OR execute_parallel()
    ↓
For each scenario:
    - Check scenario.is_valid()
    - If invalid: Create failed ProcessResult + broadcast status + skip
    - If valid: Submit to ProcessExecutor
    ↓
ProcessExecutor.run(shared_data, live_queue)
    ↓
process_main(config, shared_data, live_queue)  ← Subprocess Entry Point
    ↓
Returns ProcessResult to main process
```

### Auto-Detection Logic

```python
# Debugger detection
DEBUGGER_ACTIVE = (
    hasattr(sys, 'gettrace') and sys.gettrace() is not None
    or 'debugpy' in sys.modules
    or 'pydevd' in sys.modules
)

# Mode selection
if DEBUGGER_ACTIVE or os.getenv('DEBUG_MODE'):
    use_processpool = False  # ThreadPool for compatibility
else:
    use_processpool = True   # ProcessPool for performance
```

**Why Auto-Detection?**
- Debuggers create sockets inherited by fork()
- VSCode debugpy causes 10+ second shutdown delays in ProcessPool
- ThreadPool works seamlessly with debuggers (but slower)
- Production runs without debugger use ProcessPool for maximum speed

---

## Process Startup Preparation (Subprocess)

### Why Objects Created in Subprocess?

**Critical Design Decision:** All complex objects (Workers, TradeSimulator, Logger) are created INSIDE the subprocess, not pickled from main process.

**Rationale:**
1. **Unpickleable Objects:** ThreadPools, file handles, logging handlers cannot be pickled
2. **Clean Isolation:** Each subprocess has independent state (no shared contamination)
3. **Memory Efficiency:** Only serializable config + data sent over pipe
4. **Crash Safety:** Subprocess crash doesn't corrupt main process

### Startup Preparation Steps

**Function:** `process_startup_preparation(config, shared_data, logger)`  
**Located:** `python/framework/process/process_startup_preparation.py`

**Process:**

```
1. Create ScenarioLogger
   └─> Dedicated log file per scenario
   └─> Uses shared run_timestamp from config

2. Create Workers from strategy_config
   └─> WorkerFactory.create_workers_from_config()
   └─> Each worker initialized with parameters

3. Create Decision Logic
   └─> DecisionLogicFactory.create_logic()
   └─> Configured from decision_logic_config

4. Create WorkerCoordinator
   └─> Links workers + decision logic
   └─> Configures parallel execution (if enabled)

5. Create TradeSimulator
   └─> Loads broker config from shared_data
   └─> Initializes PortfolioManager
   └─> Injects DecisionTradingAPI into decision logic

6. Create BarRenderingController
   └─> Register workers requiring bar data
   └─> Inject warmup bars matching (symbol, timeframe, start_time)
   └─> Validate warmup bar injection

7. Deserialize Ticks
   └─> Convert immutable tuple → mutable list
   └─> Enables tick processing in loop
```

**Timing:** ~60ms for typical scenario (2 workers, 2.3k ticks)

### Warmup Bar Injection

**Key Logic:** Match bars by (symbol, timeframe, start_time) tuple

```python
for key, bars_tuple in shared_data.bars.items():
    symbol, timeframe, start_time = key
    
    # Only inject bars matching this scenario
    if symbol == config.symbol and start_time == config.start_time:
        warmup_bars[timeframe] = bars_tuple

bar_rendering_controller.inject_warmup_bars(
    symbol=config.symbol, 
    warmup_bars=warmup_bars
)
```

**Example from Log:**
```
🔍 Checking: (GBPUSD, M5, 2025-09-25 14:00:00+00:00)
  ✅ MATCH! Adding 14 bars
🔍 Checking: (GBPUSD, M30, 2025-09-25 14:00:00+00:00)
  ✅ MATCH! Adding 20 bars
✅ Injected warmup bars: M5:14, M30:20
```

---

## Tick Loop Execution

### 6-Step Processing Pipeline

**Function:** `execute_tick_loop(config, prepared_objects, live_queue)`  
**Located:** `python/framework/process/process_tick_loop.py`

**Per-Tick Operations:**

```python
for tick_idx, tick in enumerate(ticks):
    
    # Step 1: Update Prices (Portfolio Valuation)
    trade_simulator.update_prices(tick)
    
    # Step 2: Render Bars (Vectorized)
    current_bars = bar_rendering_controller.process_tick(tick)
    
    # Step 3: Retrieve Bar History
    bar_history = bar_rendering_controller.get_all_bar_history(symbol)
    
    # Step 4: Worker Processing + Decision
    decision = worker_coordinator.process_tick(
        tick=tick,
        current_bars=current_bars,
        bar_history=bar_history
    )
    
    # Step 5: Execute Decision (Order Placement/Management)
    decision_logic.execute_decision(decision, tick)
    
    # Step 6: Live Update (Time-based Throttling)
    process_live_export(live_setup, config, tick_idx, ...)
```

### Profiling System

**Per-Operation Timing:**
```python
profile_times = defaultdict(float)
profile_counts = defaultdict(int)

t1 = time.perf_counter()
trade_simulator.update_prices(tick)
t2 = time.perf_counter()
profile_times['trade_simulator'] += (t2 - t1) * 1000
profile_counts['trade_simulator'] += 1
```

**Tracked Operations:**
- `trade_simulator` - Price updates + portfolio valuation
- `bar_rendering` - Vectorized bar creation
- `bar_history` - Historical bar retrieval
- `worker_decision` - Worker execution + decision logic
- `order_execution` - Order placement + management
- `live_update` - Queue export (if enabled)

---

## ProcessPool vs ThreadPool Architecture

### Comparison Table

| Feature | ThreadPool | ProcessPool |
|---------|-----------|-------------|
| **Parallelism** | ❌ GIL-limited (quasi-sequential) | ✅ True parallel (no GIL) |
| **Performance (3 scenarios @ 3.5s)** | ~12s total | ~4-5s total |
| **Startup** | Fast (<10ms) | Slow (~50-100ms per worker) |
| **Shutdown** | Instant (<10ms) | Normal: ~50ms / Bug: 10+ seconds |
| **Debugger** | ✅ Perfect compatibility | ❌ Socket inheritance issues |
| **File Handles** | ✅ No issues | ⚠️ Requires explicit cleanup |
| **Memory** | Shared (GIL-protected) | Isolated (CoW-optimized) |
| **Use Case** | Development + debugging | Production + large batches |

### ThreadPool Characteristics

**Advantages:**
- Instant startup and shutdown
- Works seamlessly with debuggers (VSCode, PyCharm)
- No file handle inheritance issues
- Simple resource management

**Limitations:**
- Python GIL prevents true parallelism
- CPU-bound tasks run quasi-sequentially
- 10+ scenarios see minimal speedup
- Performance degradation with load

**Best For:**
- Development with debugger attached
- Small batches (1-5 scenarios)
- Quick testing iterations

### ProcessPool Characteristics

**Advantages:**
- **True Parallelism:** No GIL limitations
- **3-4x Faster:** Large batches (10+ scenarios)
- **Optimal CPU Utilization:** All cores active
- **Memory Isolation:** Clean subprocess separation

**Challenges:**
- **Startup Overhead:** ~50-100ms per worker (fork cost)
- **Resource Cleanup:** Must explicitly close file handles, loggers
- **Debugger Issues:** VSCode debugpy inherits sockets → 10s shutdown delay
- **Pickle Requirements:** Only serializable data can cross process boundary

**Best For:**
- Production runs (no debugger)
- Large batches (10-1000+ scenarios)
- Maximum performance requirements

### Critical Cleanup Requirements

**Why Cleanup Matters:**

ProcessPool uses `fork()` on Linux, which copies entire process memory including ALL open file handles and sockets. If not closed properly, Python waits for them to timeout (~10 seconds) during shutdown.

**Required Cleanups:**

```python
# In process_tick_loop.py - after execution
1. worker_coordinator.cleanup()     # Close ThreadPool (if enabled)
2. scenario_logger.close()          # Close log file handles
3. logging.shutdown()               # Close ALL Python logging handlers
```

**Example from Code:**
```python
# Close open trades
trade_simulator.close_all_remaining_orders()

# Cleanup coordinator
worker_coordinator.cleanup()
logger.debug("✅ Coordinator cleanup completed")
```

### Fork Startup Methods

**Available Methods:**

| Method | Startup Speed | Cleanliness | Default |
|--------|--------------|-------------|---------|
| `fork` | ⚡ Fast | ⚠️ Inherits everything | Linux |
| `spawn` | 🐌 Very slow (~1s/process) | ✅ Clean | Windows |
| `forkserver` | 🟡 Medium | ✅ Clean fork from server | Optional |

**Fork Issues:**
- Inherits debugger sockets
- Inherits file handles
- Inherits thread state

**Recommendation:**
- **Production:** Use fork (fast, explicitly cleanup)
- **Debugging:** Use ThreadPool (avoid fork entirely)
- **Alternative:** Use forkserver (compromise solution)

---

## Live Progress System

### Queue-Based Non-Blocking Updates

**Architecture:**
```
Subprocess (process_live_export)
    ↓ Queue.put_nowait()
Main Process (live_progress_display)
    ↓ Queue.get_nowait()
Display Update (Rich Console)
```

**Key Properties:**
- **Non-blocking:** Never blocks tick loop
- **Lossy:** Drops updates if queue full (acceptable)
- **Time-based:** Throttled by update_interval_sec
- **Conditional:** Exports only requested data

### Live Update Lifecycle

**Setup Phase:**
```python
live_setup = process_live_setup(
    logger=scenario_logger,
    config=config,
    ticks=ticks,
    live_queue=live_queue
)
```

**Per-Tick Check:**
```python
current_time = time.perf_counter()
time_since_last = current_time - live_setup.last_update_time

if time_since_last >= update_interval_sec or is_last_tick:
    # Build and send update
    process_live_export(...)
```

**Configuration:**
```json
{
  "live_stats_config": {
    "enabled": true,
    "update_interval_sec": 0.30,
    "export_portfolio_stats": false,
    "export_performance_stats": false,
    "export_current_bars": false
  }
}
```

### Update Message Structure

**Core Message (Always):**
```python
{
    "type": "progress",
    "scenario_index": 1,
    "scenario_name": "window_07",
    "symbol": "GBPUSD",
    "ticks_processed": 15000,
    "total_ticks": 20000,
    "progress_percent": 75.0,
    "status": "running",
    "current_balance": 9950.00,
    "total_trades": 5
}
```

**Conditional Exports (Expensive - Optional):**
```python
if config.live_stats_config.export_portfolio_stats:
    portfolio_stats = portfolio.get_portfolio_statistics()  # Heavy
    live_data["portfolio_stats"] = asdict(portfolio_stats)

if config.live_stats_config.export_performance_stats:
    live_data["performance_stats"] = worker_coordinator.get_snapshot()

if config.live_stats_config.export_current_bars:
    live_data["current_bars"] = serialize_current_bars(current_bars)
```

### Status Broadcasting

**Status Lifecycle:**
```python
INIT_PROCESS       # Subprocess started
    ↓
RUNNING            # Tick loop active
    ↓
COMPLETED          # Successful completion
    or
FINISHED_WITH_ERROR  # Validation failed or execution error
```

**Broadcast Function:**
```python
broadcast_status_update(
    live_queue=live_queue,
    scenario_index=idx,
    scenario_name=scenario.name,
    status=ScenarioStatus.RUNNING,
    live_stats_config=live_stats_config
)
```

### Throttling Strategy

**Why Throttle?**
- Queue fills quickly at high tick rates (20k ticks = potential 20k messages)
- Display cannot update 1000x/second
- CPU overhead for message serialization
- Network overhead if exported to external systems

**Solution:** Time-based throttling (default: 0.30s = ~3 updates/second)

**Example from Log:**
```
Scenario: window_07
Ticks: 20,000
Duration: 1.5s
Live update count: 5  ← Only 5 updates despite 20k ticks
```

**Update Frequency:**
```
20,000 ticks / 5 updates = 4,000 ticks/update
1.5s / 5 updates = 0.3s/update  ✅ Matches config
```

---

## Serialization & CoW Optimization

### Immutable Tuple Strategy

**ProcessDataPackage Design:**

```python
@dataclass
class ProcessDataPackage:
    # Immutable tuples for CoW
    ticks: Dict[str, Tuple[Any, ...]]
    bars: Dict[Tuple[str, str, datetime], Tuple[Any, ...]]
    broker_configs: Tuple[str, Tuple[Tuple[str, Any], ...]]
```

**Why Tuples?**

Python's pickle serialization optimizes differently for immutable vs mutable types:

| Type | Pickle Behavior | Memory |
|------|----------------|--------|
| `list` | Defensive copy (assumes mutation) | Full copy |
| `tuple` | Aggressive optimization (knows immutable) | CoW-shared |

### Copy-on-Write (CoW) Mechanism

**How CoW Works:**

```
Main Process                  Subprocess
┌─────────────────┐          ┌─────────────────┐
│ Tuple: [1,2,3]  │   fork   │ Tuple: [1,2,3]  │
│ Memory: 0x1000  │  ─────>  │ Memory: 0x1000  │  ← Same address!
└─────────────────┘          └─────────────────┘

Read Access (No Copy):
Subprocess reads tuple[0]  → 0x1000 (shared)

Write Access (Copy Triggered):
Subprocess creates list  → 0x2000 (new allocation)
```

**Benefits:**
- **Zero-copy sharing:** Subprocess starts instantly with data access
- **Memory efficiency:** 60k ticks shared, not copied
- **Performance:** No serialization overhead for read-only data

### Deserialization in Subprocess

**Tick Deserialization:**

```python
# In process_startup_preparation.py
ticks = process_deserialize_ticks_batch(
    scenario_name=config.name,
    scenario_symbol=config.symbol,
    ticks_tuple_list=shared_data.ticks
)
```

**Why Deserialize?**
- Tick loop needs mutable list for iteration
- Convert from immutable tuple → mutable list
- Happens ONCE per subprocess (not per tick)

**Performance:**
```
20,000 ticks deserialized in ~50ms
```

---

## Error Handling

### ProcessResult Structure

**Success Path:**
```python
ProcessResult(
    success=True,
    scenario_name="window_07",
    symbol="GBPUSD",
    scenario_index=1,
    execution_time_ms=1546.0,
    tick_loop_results=ProcessTickLoopResult(...),  # Full results
    scenario_logger_buffer=[(timestamp, message), ...]
)
```

**Error Path (Execution):**
```python
ProcessResult(
    success=False,
    scenario_name="window_07",
    symbol="GBPUSD",
    scenario_index=1,
    error_type="RuntimeError",
    error_message="Order execution failed: Insufficient margin",
    traceback="Traceback (most recent call last):\n  File ...",
    scenario_logger_buffer=[(timestamp, message), ...]
)
```

**Error Path (Validation):**
```python
ProcessResult(
    success=False,
    scenario_name="window_05",
    scenario_index=0,
    error_type="ValidationError",
    error_message="Scenario 'window_05' failed validation:\n1. start_date...",
    traceback=None,  # No traceback for validation errors
    execution_time_ms=0.0
)
```

### Error Capture in Subprocess

```python
try:
    # Startup preparation
    prepared_objects = process_startup_preparation(...)
    
    # Tick loop execution
    tick_loop_results = execute_tick_loop(...)
    
    # Build success result
    return ProcessResult(success=True, ...)
    
except Exception as e:
    # Capture error details
    log_buffer = scenario_logger.get_buffer()  # Extract logs
    
    return ProcessResult(
        success=False,
        error_type=type(e).__name__,
        error_message=str(e),
        traceback=traceback.format_exc(),
        scenario_logger_buffer=log_buffer
    )
```

### Logger Buffer Extraction

**Why Buffer?**
- Subprocess logger writes to dedicated file
- Main process needs logs for display
- Extract buffer before subprocess terminates

**Implementation:**
```python
# In subprocess (process_main.py)
log_buffer = scenario_logger.get_buffer()
scenario_logger.close()

# Return to main process
return ProcessResult(
    success=True,
    scenario_logger_buffer=log_buffer,
    ...
)
```

**Main Process Display:**
```python
for result in results:
    if result.scenario_logger_buffer:
        for timestamp, message in result.scenario_logger_buffer:
            print(f"[{timestamp}] {message}")
```

---

## Performance Analysis

### Real-World Data (window_07 - 20k ticks)

**Configuration:**
```json
{
  "symbol": "GBPUSD",
  "start_date": "2025-09-25T14:00:00+00:00",
  "max_ticks": 20000,
  "workers": {
    "rsi_fast": {"period": 14, "timeframe": "M5"},
    "envelope_main": {"period": 20, "deviation": 2.0, "timeframe": "M30"}
  }
}
```

**Execution Metrics:**
```
Total Duration:      1.546s
Ticks Processed:     20,000
Avg Time/Tick:       0.073ms
Tick Timespan:       2h 13m (14:00:00 → 16:13:23)
Live Updates:        5 (throttled)
```

### Per-Operation Breakdown

**Profiling Results:**

| Operation | Total Time | Avg/Call | Calls | % of Total |
|-----------|-----------|----------|-------|-----------|
| `worker_decision` | 944.64ms | 0.047ms | 20,000 | **65.0%** ⚠️ |
| `bar_rendering` | 363.52ms | 0.018ms | 20,000 | 25.0% |
| `order_execution` | 62.23ms | 0.003ms | 20,000 | 4.3% |
| `trade_simulator` | 35.45ms | 0.002ms | 20,000 | 2.4% |
| `live_update` | 16.61ms | 0.001ms | 20,000 | 1.1% |
| `bar_history` | 3.89ms | 0.000ms | 20,000 | 0.3% |

**Analysis:**
- ✅ `worker_decision` at 65% is expected (core strategy logic)
- ✅ `bar_rendering` at 25% is efficient (vectorized implementation)
- ✅ `order_execution` at 4.3% shows low overhead
- ✅ `live_update` at 1.1% confirms throttling effectiveness

### Worker Decision Breakdown

**Components:**
```
Total: 944.64ms

Worker Execution:       743.60ms (78.7%)
  ├─ rsi_fast:          398.77ms (42.2%)
  └─ envelope_main:     344.83ms (36.5%)

Decision Logic:         135.28ms (14.3%)
  └─ aggressive_trend:  135.28ms

Coordination Overhead:   65.77ms (7.0%)
```

**Interpretation:**
- Workers consume majority of decision time (expected)
- Decision logic at 14.3% is reasonable
- Coordination overhead at 7% is acceptable

### Startup Preparation Performance

**From Log:**
```
[2s 896ms] Starting scenario: window_07
[2s 959ms] Process preparation finished
────────────────────────────────────────────
Startup Time: 63ms  ✅ Fast
```

**Breakdown:**
```
Worker Creation:         ~5ms  (2 workers)
Decision Logic:          ~3ms
WorkerCoordinator:       ~5ms
TradeSimulator:          ~10ms (broker config load)
BarRenderingController:  ~5ms (warmup injection)
Tick Deserialization:    ~35ms (20k ticks)
────────────────────────────────────────────
Total:                   ~63ms
```

### Comparison: Small vs Large Scenario

| Metric | window_05 (2.3k ticks) | window_07 (20k ticks) |
|--------|----------------------|---------------------|
| **Duration** | 812ms | 1,546ms |
| **Avg/Tick** | 0.340ms | 0.077ms |
| **worker_decision %** | 29.1% | 65.0% |
| **trade_simulator %** | 29.2% | 2.4% |
| **Live Updates** | 3 | 5 |

**Why Smaller Scenario Slower Per-Tick?**
- Fixed overhead (startup, warmup) amortized over fewer ticks
- Trade execution overhead higher (5 trades / 2.3k ticks = 0.2%)
- Smaller batches less cache-efficient

---

## Design Decisions

### 1. Subprocess Object Creation
**Rationale:** Complex objects (loggers, pools, file handles) unpickleable  
**Benefit:** Clean isolation, no shared state contamination  
**Trade-off:** ~60ms startup overhead per subprocess (acceptable)

### 2. CoW Optimization via Tuples
**Rationale:** ProcessPool benefits from zero-copy data sharing  
**Benefit:** 60k ticks shared instantly, no serialization cost  
**Trade-off:** Must deserialize to mutable list in subprocess (minimal cost)

### 3. Time-Based Live Update Throttling
**Rationale:** Display cannot update 1000x/second, queue fills quickly  
**Benefit:** ~1% overhead, stable queue performance  
**Trade-off:** Lower update frequency (acceptable for monitoring)

### 4. ProcessPool fork() on Linux
**Rationale:** Fastest startup method available  
**Benefit:** ~50ms startup vs ~1s for spawn  
**Trade-off:** Requires explicit cleanup, debugger issues

### 5. Auto-Detection vs Manual Switch
**Rationale:** Developers forget to switch modes  
**Benefit:** Automatic optimal mode selection  
**Trade-off:** May surprise users (documented with warnings)

### 6. Per-Scenario Logger Files
**Rationale:** Parallel execution with shared logger causes race conditions  
**Benefit:** Clean logs, no corruption, easy debugging  
**Trade-off:** More files (acceptable, organized by timestamp)

### 7. 6-Step Tick Loop Pipeline
**Rationale:** Clear separation of concerns, profiling granularity  
**Benefit:** Identify bottlenecks per operation  
**Trade-off:** Slight overhead from profiling (< 1%)
