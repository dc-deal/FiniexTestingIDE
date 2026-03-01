# Batch Preparation System - Phase Overview

## Architecture Overview

The batch orchestrator coordinates scenario execution through 7 distinct phases, each with clear responsibilities and optimized for performance.

---

## Phase Structure

```
┌─────────────────────────────────────────────────────────────────┐
│                    BATCH ORCHESTRATOR WORKFLOW                  │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  Phase 1: Index & Coverage Setup                               │
│  ├─ Load tick index                                            │
│  └─ Generate coverage reports                                  │
│                                                                 │
│  Phase 2: Availability Validation                              │
│  ├─ Validate date logic (end >= start)                         │
│  ├─ Check coverage report availability                         │
│  └─ Validate date ranges within available data                 │
│                                                                 │
│  Phase 3: Requirements Collection                              │
│  ├─ Collect tick requirements                                  │
│  ├─ Collect bar requirements                                   │
│  └─ Deduplicate requirements                                   │
│                                                                 │
│  Phase 4: Data Loading                                         │
│  ├─ Load ticks from parquet                                    │
│  ├─ Load warmup bars                                           │
│  └─ Prepare broker configurations                              │
│                                                                 │
│  Phase 5: Quality Validation                                   │
│  ├─ Validate tick stretch gaps                                 │
│  ├─ Validate warmup quality                                    │
│  └─ Filter scenarios with quality issues                       │
│                                                                 │
│  Phase 6: Execution                                            │
│  ├─ Parallel execution (ProcessPoolExecutor)                   │
│  └─ Sequential execution (single scenario)                     │
│                                                                 │
│  Phase 7: Summary & Reporting                                  │
│  ├─ Build BatchExecutionSummary                                │
│  ├─ Error handling                                             │
│  └─ Flush logs                                                 │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

---

## Phase Details

### Phase 1: Index & Coverage Setup
**Purpose:** Load metadata indices and generate coverage reports for all symbols  
**Mode:** Serial (main process)  
**Key Operations:**
- `TickIndexManager.build_index()` - Load or rebuild tick index
- `DataCoverageReportManager.generate_reports()` - Analyze data coverage and gaps

**Performance:** Fast (index-based, <1s for 8 symbols)

---

### Phase 2: Availability Validation
**Purpose:** Validate scenario date ranges before expensive data loading  
**Mode:** Serial (main process)  
**Key Operations:**
- Validate date logic (end_date >= start_date)
- Check coverage report availability
- Validate start/end dates within available data range

**Output:** 
- Valid scenarios proceed to Phase 3
- Invalid scenarios marked with validation_result

**Performance:** Negligible (<100ms)

**Benefits:**
- Prevents expensive data loading for invalid scenarios
- Clear error messages for configuration issues
- No crashes - batch continues with valid scenarios

---

### Phase 3: Requirements Collection & Parameter Validation
**Purpose:** Aggregate data requirements and validate parameter schemas  
**Mode:** Serial (main process)  
**Key Operations:**
- Resolve worker classes via factory registry (no instantiation)
- Validate structural config via `validate_config()` classmethod (periods, timeframes)
- Validate algorithm parameters via `validate_parameter_schema()` classmethod (min/max/type)
- Collect tick requirements (symbol, start_time, max_ticks)
- Collect bar requirements via `calculate_requirements()` classmethod
- Deduplicate overlapping requirements

**Parameter Validation:**
- Uses `strict_parameter_validation` from `execution_config` (default: `true`)
- Strict mode: abort scenario on boundary violation
- Non-strict mode: log warning, continue execution
- Type errors always abort (regardless of strict flag)

**Input:** All scenarios (skips invalid internally)  
**Output:** RequirementsMap for Phase 4

**Performance:** Fast (~50ms for 3 scenarios)

---

### Phase 4: Data Loading
**Purpose:** Load shared data once for all scenarios  
**Mode:** Serial (main process)  
**Key Operations:**
- Load ticks from parquet (based on RequirementsMap)
- Load warmup bars (filtered to start_time)
- Prepare broker configurations
- Package into ProcessDataPackage

**Optimization:** 
- Copy-on-Write (CoW) sharing in subprocesses
- Deduplication prevents duplicate loads
- Only loads for scenarios in RequirementsMap

**Performance:** I/O bound (1-5s for 30k ticks)

---

### Phase 5: Quality Validation
**Purpose:** Validate quality of loaded data  
**Mode:** Serial (main process)  
**Key Operations:**
- Validate tick stretch gaps (no large gaps in loaded data)
- Validate warmup bar quality (no synthetic bars in standard mode)
- Filter scenarios with quality issues

**Input:** Pre-filtered scenarios (only those that passed Phase 2)  
**Output:** Final valid scenarios for execution

**Performance:** Fast (~50ms)

**Note:** This phase only validates quality aspects. Availability was already validated in Phase 2.

---

### Phase 6: Execution
**Purpose:** Execute scenarios with prepared data  
**Mode:** Parallel (ProcessPoolExecutor) or Sequential  
**Key Operations:**
- WorkerFactory / DecisionLogicFactory validate parameter schemas (second check)
- Apply schema defaults to config (fill missing optional parameters)
- Create ProcessExecutor for each scenario
- Run tick loop with trading simulation
- Collect execution results

**Optimization:**
- ProcessPool for true parallelism (no GIL)
- ThreadPool fallback when debugger detected
- Skip invalid scenarios (checks is_valid())

**Performance:** CPU bound (depends on tick count and strategy complexity)

---

### Phase 7: Summary & Reporting
**Purpose:** Build comprehensive execution summary  
**Mode:** Serial (main process)  
**Key Operations:**
- Aggregate results from all scenarios
- Calculate portfolio metrics
- Build BatchExecutionSummary
- Error handling for failed scenarios

**Performance:** Fast (<1s)

---

## Validation Flow

### Three-Stage Validation Design

```
Phase 2: Availability Validation (PRE-LOAD)
├─ Check: Date logic (end >= start)
├─ Check: Coverage report exists
└─ Check: Dates within available data
   ↓
   [Filter invalid scenarios]
   ↓
Phase 3: Requirements & Parameter Validation (PRE-LOAD)
├─ Structural: validate_config() (periods, timeframes)
├─ Algorithm: validate_parameter_schema() (min/max/type)
└─ Collect bar/tick requirements via classmethods
   ↓
   [Abort on type errors or strict boundary violations]
   ↓
Phase 4: Data Loading
   (Only for valid scenarios)
   ↓
Phase 5: Quality Validation (POST-LOAD)
├─ Check: Tick stretch gaps
└─ Check: Warmup bar quality
   ↓
   [Filter scenarios with quality issues]
   ↓
Phase 6: Execution (with second parameter validation)
   (Only final valid scenarios)
```

### Benefits of Two-Stage Validation
1. **Early filtering** - Invalid scenarios don't trigger expensive data loading
2. **Clear separation** - Availability vs Quality concerns
3. **No crashes** - Batch continues with valid scenarios
4. **Detailed errors** - Users get specific validation failure reasons

---

## Error Handling Strategy

### Validation Errors (Non-Fatal)
- Scenarios with validation errors are skipped
- ProcessResult created with ValidationError type
- Batch continues with remaining valid scenarios
- Summary includes all scenarios (valid + invalid)

### Runtime Errors (Scenario-Level)
- Caught in process_main() exception handler
- ProcessResult created with error details
- Other scenarios continue execution
- Summary includes failed scenario

### Critical Errors (Batch-Level)
- Index loading failures
- Coverage report generation failures
- Data loading failures for all scenarios
- These abort the entire batch

---

## Performance Characteristics

### Serial Phases (1-5)
**Total:** ~2-10 seconds (depending on data size)
- Phase 1: <1s (index is cached)
- Phase 2: <0.1s (validation only)
- Phase 3: <0.1s (in-memory operations)
- Phase 4: 1-8s (I/O bound - parquet loading)
- Phase 5: <0.1s (validation only)

### Parallel Phase (6)
**Total:** Varies (CPU bound)
- Depends on: tick count, strategy complexity, CPU cores
- Example: 3 scenarios × 10k ticks each = ~3-5s (parallel)

### Summary Phase (7)
**Total:** <1s (report generation)

---

## Key Design Principles

1. **Fail Fast** - Validate before expensive operations
2. **No Silent Failures** - Clear error messages for all validation failures
3. **Graceful Degradation** - Invalid scenarios don't crash the batch
4. **Resource Efficiency** - Load data once, share via CoW
5. **Clear Separation** - Each phase has single responsibility

---

## Migration Notes

### From Old Phase Numbering

**Old:**
- Phase 0: Requirements
- Phase 0.5: Availability Validation (informal)
- Phase 1: Data Preparation
- Phase 1.5: Quality Validation
- Phase 2: Execution

**New:**
- Phase 1: Index & Coverage Setup
- Phase 2: Availability Validation
- Phase 3: Requirements Collection
- Phase 4: Data Loading
- Phase 5: Quality Validation
- Phase 6: Execution
- Phase 7: Summary

**Benefits:**
- Sequential numbering (easier to track)
- Clear phase boundaries
- No decimal phases (0.5, 1.5)

---

## Logger Message Format

Each phase logs with consistent format:

```
Phase 1: Index & coverage setup...
Phase 2: Validating data availability...
Phase 3: Collecting data requirements...
Phase 4: Loading data...
Phase 5: Validating data quality...
Phase 6: Executing scenarios...
Phase 7: Building summary...
```

---

## Common Issues & Solutions

### Issue: "start_date is BEFORE available data range"
**Phase:** 2 (Availability Validation)  
**Solution:** Adjust start_date in scenario config to match available data

### Issue: "Large gap detected in tick stretch"
**Phase:** 5 (Quality Validation)  
**Solution:** Adjust start_date to avoid gap period, or use permissive mode

### Issue: "Only X warmup bars available (requested Y)"
**Phase:** 4 (Data Loading) - Warning only  
**Solution:** Adjust start_date earlier, or accept partial warmup

### Issue: Scenario skipped with no clear error
**Check:** scenario.validation_result for detailed error info  
**Location:** Either Phase 2 or Phase 5

