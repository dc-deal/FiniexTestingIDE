# Contributing to FiniexTestingIDE

Thank you for your interest in contributing. This document covers the essentials for getting started.

---

## Development Environment

FiniexTestingIDE runs inside a Docker dev container. Open the repository in VS Code and select **Dev Containers: Reopen in Container** — the container builds automatically.

Requirements: Docker Desktop, VS Code with the [Dev Containers](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-containers) extension.

---

## Code Guidelines

Full code guidelines (`CODE_GUIDELINES.md`) and automated enforcement via CI are planned — see **[Issue #30](https://github.com/dc-deal/FiniexTestingIDE/issues/30)**.

Core principles in the meantime:
- **English only** — code, comments, and documentation
- **Fully typed** — type hints on all function signatures
- **UTC everywhere** — all datetime objects must be timezone-aware UTC
- **No `__init__.py`** — fully qualified import paths throughout

---

## Running Tests

```bash
python python/cli/test_runner_cli.py
```

Individual suites: `pytest tests/<suite>/ -v`

---

## Visual Frontend (FiniexViewer)

To work on or use the FiniexViewer companion UI, see the setup guide:
→ [FiniexViewer Dev Setup](docs/user_guides/finiexviewer_setup.md)

---

## Pull Requests

- Branch from `dev`, target `dev` for PRs (not `main`)
- One logical change per PR
- Include a brief description of what changed and why
- All tests must pass before merge
