# Blackbox Workers

This directory contains IP-protected trading strategy workers.

## Purpose
- Add your proprietary, closed-source trading strategies here
- Workers inherit from `AbstractBlackboxWorker`
- Strategy logic remains hidden while exposing parameters via contracts

## Example Structure

```
blackbox/
├── my_strategy_v1.py
├── advanced_momentum.py
└── .gitignore          # Add sensitive workers to .gitignore!
```

## Getting Started
1. Copy a template from `framework/workers/` (e.g., `rsi_worker.py`)
2. Implement your strategy logic in `compute()`
3. Define your parameters via `get_contract()`
4. Reference in scenario config: `"worker_types": ["my_strategy_v1"]`

**Remember:** Add sensitive files to `.gitignore`!