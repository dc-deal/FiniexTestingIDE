# User Workers

This directory contains open-source, custom trading strategy workers.

## Purpose
- Add your experimental or open-source strategies here
- Share and collaborate on trading logic
- Learn from examples and build your own

## Example Structure

```
user/
├── my_experimental_rsi.py
├── custom_bollinger.py
└── trend_follower.py
```


## Getting Started
1. Copy a template from `framework/workers/` (e.g., `rsi_worker.py`)
2. Implement your custom logic
3. Reference in scenario config: `"worker_types": ["my_experimental_rsi"]`

**Tip:** These workers are tracked in Git by default. Move sensitive strategies to `blackbox/`.