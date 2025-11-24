# Batch Preparation & Validation System
**FiniexTestingIDE - Data Quality Architecture**

---

## Overview

The batch preparation system ensures **authentic market simulation** through comprehensive data quality validation. Invalid scenarios are gracefully skipped rather than aborting entire batch runs, enabling robust production deployments.

**Key Principle:** Never execute scenarios with compromised data quality.

---

## Four-Phase Workflow

### Phase 0: Requirements Collection
**Responsibility:** Aggregate tick and bar requirements from all scenarios

**Process:**
1. Iterate through scenarios and extract requirements
2. Calculate warmup needs based on worker configurations
3. Deduplicate overlapping requirements
4. Package into `RequirementsMap`

**Output:** `RequirementsMap` with `tick_requirements` and `bar_requirements`

---

### Phase 0.5: Gap Report Generation (NEW)
**Responsibility:** Analyze data coverage and identify gaps

**Process:**
1. Extract unique symbols from scenarios
2. Load tick index metadata (no data I/O)
3. Generate `CoverageReport` per symbol with gap classification:
   - **SEAMLESS:** < 5 seconds
   - **SHORT:** 5s - 30 minutes
   - **WEEKEND:** 40-80 hours (Friday → Monday)
   - **MODERATE:** 30 minutes - 4 hours
   - **LARGE:** > 4 hours
4. Cache reports for Phase 1.5 validation

**Output:** `Dict[str, CoverageReport]` cached in `CoverageReportManager`

**Location:** `python/framework/batch/coverage_report_manager.py`

---

### Phase 1: Data Preparation
**Responsibility:** Load ticks, bars, and broker configurations

**Process:**
1. Load ticks (gap-aware, auto-skips to first available)
2. Load warmup bars (before start_date)
3. Load broker configurations
4. Package as immutable tuples (CoW optimization)

**Output:** `ProcessDataPackage` with loaded data

**Key Behavior:** Tick loading auto-adjusts to first available tick after gaps

---

### Phase 1.5: Quality Validation (NEW)
**Responsibility:** Validate data quality before execution

**Process:**
1. Iterate through scenarios
2. For each scenario, perform three checks:
   - **Check 1:** start_date not inside gap
   - **Check 2:** Tick stretch free of forbidden gaps
   - **Check 3:** Warmup bars contain no synthetic data (standard mode)
3. Create `ValidationResult` per scenario
4. Mark scenarios with validation result
5. Return lists of valid and invalid scenarios

**Output:** 
- `valid_scenarios: List[SingleScenario]` 
- `invalid_scenarios: List[Tuple[SingleScenario, ValidationResult]]`

**Location:** `python/framework/validators/scenario_data_validator.py`

**Critical Behavior:** Invalid scenarios are **marked** (not removed), enabling graceful skip in Phase 2

---

### Phase 2: Execution
**Responsibility:** Execute valid scenarios, skip invalid ones

**Process:**
1. Check `scenario.is_valid()` before execution
2. If invalid:
   - Create `ProcessResult` with `success=False`
   - Include detailed validation error report
   - Broadcast `ScenarioStatus.FINISHED_WITH_ERROR` to display
   - Continue to next scenario
3. If valid:
   - Execute normally (sequential or parallel)

**Output:** `List[ProcessResult]` containing both valid executions and validation failures

---

## Validation Checks (Phase 1.5)

### Check 1: start_date Not in Gap
**Purpose:** Ensure first tick is available at configured start_date

**Logic:**
```python
for gap in coverage_report.gaps:
    if gap.file1.end_time < start_date < gap.file2.start_time:
        ERROR: "start_date inside gap - no ticks available"
```

**Example Error:**
```
❌ start_date 2025-10-18 16:00:00 UTC is inside weekend gap 
   (2025-10-17 20:56:59 → 2025-10-19 21:00:03). 
   No ticks available! 
   Next valid start: 2025-10-19 21:00:03 UTC
```

---

### Check 2: Tick Stretch Gap-Free
**Purpose:** Ensure tick range contains only allowed gap types

**Logic:**
```python
first_tick = loaded_ticks[0].timestamp
last_tick = loaded_ticks[-1].timestamp

for gap in coverage_report.gaps:
    if gap overlaps [first_tick, last_tick]:
        if gap.category not in allowed_gap_categories:
            ERROR: "Forbidden gap in tick stretch"
```

**Configuration:**
```json
{
  "data_validation": {
    "allowed_gap_categories": ["seamless", "short"]
  }
}
```

**Example Error:**
```
🔴 LARGE gap detected in tick stretch 
   (2025-11-12 15:40:02 → 2025-11-13 21:15:59, 29h 35m). 
   Not allowed in 'standard' mode
```

---

### Check 3: Warmup Quality
**Purpose:** Prevent synthetic bars in warmup period

**Logic:**
```python
for bar in warmup_bars:
    if bar.bar_type == 'synthetic':
        synthetic_count += 1

if synthetic_count > 0 and mode == 'standard':
    ERROR: "Synthetic bars in warmup"
```

**Configuration:**
```json
{
  "data_validation": {
    "warmup_quality_mode": "standard"
  }
}
```

**Modes:**
- **standard:** No synthetic bars allowed (production-grade)
- **permissive:** Warnings only (testing/relaxed validation)

**Example Error:**
```
❌ Warmup for M30 contains 20/20 synthetic bars (100.0%) 
   - not allowed in standard mode. 
   Adjust start_date to avoid gaps in warmup period.
```

---

## Configuration Reference

### app_config.json - data_validation Section

```json
{
  "data_validation": {
    "warmup_quality_mode": "standard",
    "allowed_gap_categories": ["seamless", "short"]
  }
}
```

**warmup_quality_mode Options:**
- `"standard"`: Production-grade - no synthetic bars in warmup (recommended)
- `"permissive"`: Testing mode - warnings only for synthetic bars

**allowed_gap_categories Options:**
Available categories: `seamless`, `short`, `weekend`, `moderate`, `large`

**Recommended Settings:**
- **Production:** `["seamless", "short"]` - strictest quality
- **Testing:** `["seamless", "short", "weekend"]` - allows weekend gaps
- **Relaxed:** `["seamless", "short", "weekend", "moderate"]` - maximum flexibility

---

## Error Handling Philosophy

### Fail-Safe Batch Execution
**Principle:** One bad scenario should never block 99 good ones

**Behavior:**
- Invalid scenarios logged with detailed errors
- Valid scenarios continue execution
- Batch aborts only if **ALL** scenarios invalid

**Benefits:**
1. **Team Productivity:** Different team members can work on different scenarios independently
2. **Efficient Debugging:** See all validation issues at once, not one-at-a-time
3. **Production Resilience:** Partial batch results still valuable
4. **Quality Assurance:** No scenario executes with compromised data

---

## Example Log Output

### Successful Validation (Mixed Valid/Invalid)

```
📊 Phase 1.5: Quality validation...
✅ Generated 2 gap report(s)
🔍 Phase 1.5: Validating data quality...

❌ window_05: start_date 2025-10-18 16:00:00 UTC is inside weekend gap 
   (2025-10-17 20:56:59 → 2025-10-19 21:00:03). No ticks available! 
   Next valid start: 2025-10-19 21:00:03 UTC
❌ window_05: Warmup for M5 contains 14/14 synthetic bars (100.0%) 
   - not allowed in standard mode. Adjust start_date to avoid gaps.
❌ window_05: Warmup for M30 contains 20/20 synthetic bars (100.0%) 
   - not allowed in standard mode. Adjust start_date to avoid gaps.

⚠️  1/2 scenarios failed validation
⚠️  1 scenario(s) failed validation - skipped
✅ Validation complete: 1/2 scenarios valid
✅ Continuing with 1/2 valid scenario(s)

🚦 Phase 2: Executing scenarios...
⚠️  Scenario 1: window_05 - SKIPPED (validation failed)
▶️  Executing scenario 2/2: window_07
✅ Scenario 2 completed: window_07 (1ms)
```

---

### All Scenarios Invalid (Abort)

```
📊 Phase 1.5: Quality validation...
✅ Generated 5 gap report(s)
🔍 Phase 1.5: Validating data quality...

❌ scenario_A: start_date inside gap...
❌ scenario_B: Large gap in tick stretch...
❌ scenario_C: Synthetic bars in warmup...

⚠️  3/3 scenarios failed validation
❌ No valid scenarios after quality validation - aborting batch

DETAILED ERROR REPORT:

Scenario 'scenario_A' failed validation:
1. start_date 2025-10-18 16:00:00 UTC is inside weekend gap...
   Next valid start: 2025-10-19 21:00:03 UTC

Scenario 'scenario_B' failed validation:
1. LARGE gap detected in tick stretch...

Scenario 'scenario_C' failed validation:
1. Warmup for M5 contains 10/14 synthetic bars (71.4%)...

═══════════════════════════════════════════════════════
CRITICAL ERROR: All 3 scenario(s) failed validation
═══════════════════════════════════════════════════════
```

---

## Performance Characteristics

**Real-World Performance** (from production run with 2 scenarios, 2 symbols):

```
Phase 0:   Requirements Collection      ~8ms
Phase 0.5: Gap Report Generation       57ms   (2 gap reports)
Phase 1:   Data Preparation          ~650ms   (60,234 ticks, 68 bars)
Phase 1.5: Quality Validation           2ms   (2 scenarios validated)
─────────────────────────────────────────────
Total:     Data Preparation          ~717ms   ✅ Sub-second overhead
```

**Scaling Characteristics:**

**Phase 0: Requirements Collection**
- **Time:** ~5-10ms per scenario (8ms for 2 scenarios observed)
- **Complexity:** O(n) over scenarios
- **I/O:** None (CPU-bound aggregation)

**Phase 0.5: Gap Report Generation**
- **Time:** ~30ms per symbol (57ms for 2 symbols observed)
- **Complexity:** O(s × f) over symbols and files
- **I/O:** Metadata-only from parquet index (very fast)
- **Key Optimization:** Generated **once per symbol**, not per scenario
  - Example: 50 scenarios with 5 symbols → 5 gap reports (~150ms total)
  - Benefit: 10x improvement for multi-scenario batches

**Phase 1: Data Preparation**
- **Time:** ~10ms per 1,000 ticks (650ms for 60k ticks observed)
- **Complexity:** O(t + b) over ticks and bars
- **I/O:** Parquet file reading (columnar I/O, most expensive phase)

**Phase 1.5: Quality Validation**
- **Time:** ~1ms per scenario (2ms for 2 scenarios observed)
- **Complexity:** O(n × g) over scenarios and gaps (typically g < 10)
- **I/O:** None (in-memory validation)

**Total Overhead:** < 100ms for validation phases (0.5 + 1.5), data loading dominates

**Performance Confirmation:** Your observation is correct - validation runs very fast (~59ms total for Phase 0.5 + 1.5), well under 100ms. The index-based approach is highly efficient.

---

## Design Decisions

### 1. Gap Reports Once Per Symbol
**Rationale:** Multiple scenarios often use same symbol  
**Benefit:** 10x performance improvement for multi-scenario batches  
**Implementation:** Cache in `CoverageReportManager`

### 2. Validation After Data Load
**Rationale:** Need actual tick ranges and bar data for accurate validation  
**Benefit:** Accurate validation without pre-loading overhead  
**Trade-off:** Wasted I/O if scenario invalid (but rare in production)

### 3. Fail-Safe Invalid Scenario Handling
**Rationale:** One bad scenario shouldn't break entire batch  
**Benefit:** Resilient production runs, efficient debugging  
**Implementation:** Mark scenarios with `validation_result`, skip in execution

### 4. Config-Based Gap Categories
**Rationale:** User control over quality vs flexibility trade-off  
**Benefit:** Transparent, adjustable, self-documenting  
**Alternative Rejected:** Hardcoded gap thresholds (inflexible)

### 5. Immutable Tuple Storage
**Rationale:** Enable CoW (Copy-on-Write) memory sharing in parallel execution  
**Benefit:** Zero-copy data sharing across subprocesses  
**Implementation:** All data in `ProcessDataPackage` stored as tuples

### 6. Granular Error Reporting
**Rationale:** Single batch run may have 100+ scenarios - need per-scenario errors  
**Benefit:** All validation issues visible at once, no iterative debugging  
**Implementation:** Each scenario gets detailed `ValidationResult` with actionable errors

---

## Architecture Integration

### Component Responsibilities

**CoverageReportManager** (`coverage_report_manager.py`)
- Generate gap reports (Phase 0.5)
- Coordinate validation (Phase 1.5)
- Mark scenarios with validation results
- Return valid/invalid scenario lists

**ScenarioDataValidator** (`scenario_data_validator.py`)
- Perform three validation checks
- Generate detailed error messages
- Create `ValidationResult` objects

**ExecutionCoordinator** (`execution_coordinator.py`)
- Check `scenario.is_valid()` before execution
- Skip invalid scenarios gracefully
- Create failed `ProcessResult` for invalid scenarios
- Broadcast status updates to display

**BatchOrchestrator** (`batch_orchestrator.py`)
- Orchestrate all four phases
- Log validation summary
- Abort only if all scenarios invalid

---

## Display Integration

### Status Updates for Invalid Scenarios

**Status Broadcast:**
```python
broadcast_status_update(
    live_queue=live_queue,
    scenario_index=idx,
    scenario_name=scenario.name,
    status=ScenarioStatus.FINISHED_WITH_ERROR,
    live_stats_config=live_stats_config
)
```

**Display Shows:**
```
╭─────────────────────── 🔬 Strategy Execution Progress ───────────────────────╮
│  ❌  window_05         SKIPPED       $       0 (+$  0.00)                    │
│  ✅  window_07         Completed     $  10,000 (+$  0.00)                    │
╰──────────────────────────────────────────────────────────────────────────────╯
```

**Final Report Includes Invalid Scenarios:**
```
┌────────────────────────────────────┐
│ ❌ window_05                        │
│ Symbol: EURGBP                     │
│                                    │
│ Error: ValidationError             │
│ Scenario 'window_05' failed        │
│ validation:                        │
│ 1. start_date 2025-10-18 16:00:00  │
│    UTC is inside weekend gap...    │
│ 2. Warmup for M5 contains 14/14    │
│    synthetic bars (100.0%)...      │
│ 3. Warmup for M30 contains 20/20   │
│    synthetic bars (100.0%)...      │
└────────────────────────────────────┘
```