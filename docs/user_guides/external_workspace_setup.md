# External Workspace Setup (VSCode)

## Overview

When using external directories for USER workers or decision logics (mounted into the Docker container), VSCode needs to know where the framework imports come from. Without this, Pylance/Pyright will show import errors and autocomplete won't work.

---

## Directory Layout

Typical setup on the host machine (Windows/Mac/Linux):

```
C:\Users\<you>\projects\
├── FiniexTestingIDE\          <- Main project (mounted as /app)
└── FiniexExternalAlgos\       <- Your external workers
    ├── .vscode/
    │   └── settings.json
    ├── pyrightconfig.json
    └── envelope_mod_ext_worker.py
```

---

## Step 1: Mount the External Directory

The base `docker-compose.yml` contains only the standard volumes. To add external directories, create a `docker-compose.override.yml` in the project root (git-ignored, user-specific):

```yaml
services:
  finiex-dev:
    volumes:
      - ../FiniexExternalAlgos:/ext_algos:ro
```

Docker Compose automatically merges `docker-compose.override.yml` into `docker-compose.yml` — no flags needed. The `:ro` (read-only) is optional but recommended since the framework only reads from external dirs.

**No override file?** No problem. The container starts normally without external volumes.

## Step 2: Register the Path in `user_configs/app_config.json`

Create (or edit) `user_configs/app_config.json` to override the external paths. This file is gitignored and won't affect other users (see [config_cascade_readme.md](../config_cascade_readme.md) for the full override system):

```json
{
    "paths": {
        "user_worker_dirs": ["/ext_algos"]
    }
}
```

The container path (`/ext_algos`) must match the mount target from Step 1. The base `configs/app_config.json` ships with empty arrays — your override adds only what you need.

## Step 3: VSCode Configuration

For Pylance/Pyright to resolve framework imports in external files, add the main project as an extra path. Choose one:

### Option A: `pyrightconfig.json` (in external workspace root)

```json
{
    "extraPaths": [
        "../FiniexTestingIDE"
    ]
}
```

### Option B: `.vscode/settings.json` (in external workspace root)

```json
{
    "python.analysis.extraPaths": [
        "../FiniexTestingIDE"
    ]
}
```

Both achieve the same result. The relative path points from the external workspace to the main project directory.

---

## Why This Works

External worker files import framework modules like:

```python
from python.framework.workers.abstract_worker import AbstractWorker
from python.framework.logging.scenario_logger import ScenarioLogger
```

- **At runtime** (inside Docker): `/app` is in `sys.path`, so imports resolve normally. External files are loaded via `importlib.util.spec_from_file_location()` — no `sys.path` pollution.
- **In VSCode** (on host): `extraPaths` tells Pylance where to find the `python.*` package tree. This enables autocomplete, go-to-definition, and type checking.

---

## See Also

- [user_modules_and_hot_reload_mechanics.md](user_modules_and_hot_reload_mechanics.md) — Scanning, registration, and import mechanics
- [quickstart_guide.md](quickstart_guide.md) — Creating your first worker and decision logic
