# GIL Benchmark - Scientific Field Study

## 🎯 Purpose

This experiment scientifically proves that Python's Global Interpreter Lock (GIL) prevents true parallelism with `ThreadPoolExecutor` for CPU-bound work, and demonstrates that `ProcessPoolExecutor` is the correct solution.

## 📊 What It Tests

1. **Sequential Baseline** - Single-threaded execution (reference point)
2. **Threading (Current)** - `ThreadPoolExecutor` with N workers
3. **Multiprocessing (Solution)** - `ProcessPoolExecutor` with N workers

## 🔬 The Problem

Our current implementation in `batch_orchestrator.py` uses:
```python
with ThreadPoolExecutor(max_workers=max_workers) as executor:
    # Execute scenarios in parallel
```

**Why this fails:**
- Python's GIL allows only ONE thread to execute Python bytecode at a time
- CPU-bound work (NumPy operations, RSI calculations) can't run truly parallel
- Context switching overhead makes it SLOWER than sequential
- Your data showed: Parallel = 30.52s, Sequential = 30.60s (no gain!)

## 💡 The Solution

Replace with:
```python
from concurrent.futures import ProcessPoolExecutor

with ProcessPoolExecutor(max_workers=max_workers) as executor:
    # True parallel execution across CPU cores
```

**Why this works:**
- Each process has its own GIL
- True parallelism across CPU cores
- Expected speedup: ~N x (where N = CPU cores)
- Efficiency typically 70-90% depending on overhead

## 🚀 Running the Benchmark

### Option 1: VS Code Launch Config
Add to `.vscode/launch.json`:
```json
{
    "name": "🔬 GIL Benchmark - Threading vs Multiprocessing",
    "type": "debugpy",
    "request": "launch",
    "program": "${workspaceFolder}/python/experiments/gil_benchmark/threading_vs_multiprocessing.py",
    "console": "integratedTerminal",
    "justMyCode": false
}
```

Then press F5 and select this configuration.

### Option 2: Command Line
```bash
python python/experiments/gil_benchmark/threading_vs_multiprocessing.py
```

## 📈 Expected Results

On a system with 14 CPU cores (like your Intel 14700K):

| Method | Time | Speedup | Efficiency |
|--------|------|---------|------------|
| Sequential | 3.000s | 1.00x | 100% |
| Threading-14w | 3.100s | 0.97x | 7% |
| Multiprocessing-14w | 0.250s | 12.0x | 86% |

**Key Finding:** Threading is actually SLOWER than sequential due to GIL + overhead!

## 🔧 Applying to FiniexTestingIDE

### Changes Required in `batch_orchestrator.py`:

```python
# OLD:
from concurrent.futures import ThreadPoolExecutor

def _run_parallel(self):
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # ...

# NEW:
from concurrent.futures import ProcessPoolExecutor

def _run_parallel(self):
    with ProcessPoolExecutor(max_workers=max_workers) as executor:
        # ...
```

### Important Considerations:

1. **Pickle-able Objects** - All scenario data must be pickle-able
2. **No Shared State** - Each scenario must be independent
3. **Import Guards** - Add `if __name__ == "__main__":` guard
4. **Worker Count** - Use `multiprocessing.cpu_count()` for max efficiency

### Already Good in Your Code:

✅ Each scenario gets its own `TradeSimulator` instance  
✅ Each scenario gets its own `WorkerCoordinator` instance  
✅ Results collected via `ScenarioSetPerformanceManager` (thread-safe)  
✅ No shared mutable state between scenarios

This means the switch should be **straightforward**!

## 📝 Test Results

After running the benchmark, paste your results here:

```
[YOUR BENCHMARK RESULTS HERE]
```

## 🎓 Learning Resources

- [Python GIL Documentation](https://docs.python.org/3/glossary.html#term-global-interpreter-lock)
- [concurrent.futures](https://docs.python.org/3/library/concurrent.futures.html)
- [Multiprocessing Best Practices](https://docs.python.org/3/library/multiprocessing.html)

## ⚠️ Known Limitations

- Process creation overhead (~50-100ms per process)
- Memory overhead (each process = separate memory space)
- Not suitable for I/O bound work (use threading for that)
- Windows requires `multiprocessing.freeze_support()` call

## 🎯 Success Criteria

Benchmark proves GIL impact if:
1. Threading shows speedup < 1.5x regardless of worker count
2. Multiprocessing shows speedup > 0.7 * cpu_count
3. Efficiency difference > 60% between methods

If your results match these criteria, the GIL is definitely the bottleneck!
