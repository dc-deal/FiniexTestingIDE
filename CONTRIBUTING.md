# Contributing to FiniexTestingIDE

Thank you for your interest in contributing. This document covers the essentials for getting
started. It is deliberately short: the complete code guidelines are not written yet — see *Code
Guidelines* below.

---

## Development Environment

FiniexTestingIDE runs inside a Docker dev container. Open the repository in VS Code and select
**Dev Containers: Reopen in Container** — the container builds automatically.

Requirements: Docker Desktop, VS Code with the
[Dev Containers](https://marketplace.visualstudio.com/items?itemName=ms-vscode-remote.remote-containers)
extension.

---

## Code Guidelines

Full code guidelines (`CODE_GUIDELINES.md`) and automated enforcement via CI are planned — see
**[Issue #30](https://github.com/dc-deal/FiniexTestingIDE/issues/30)**.

Core principles in the meantime:
- **English only** — code, comments, and documentation
- **Fully typed** — type hints on all function signatures
- **UTC everywhere** — all datetime objects must be timezone-aware UTC
- **No `__init__.py`** — fully qualified import paths throughout
- **Single-quoted strings, grouped imports** — both are configured in `ruff.toml` but not checked
  by default; check the files you changed with `ruff check --select Q000,I <files>`, run from the
  repository so the configuration applies (ruff's own default is double quotes)
- **Documentation** is written to the [Documentation Style Guide](docs/documentation_style_guide.md),
  and a term means what the [Glossary](docs/glossary.md) says — look it up before introducing one

---

## Running Tests

```bash
python python/cli/test_runner_cli.py
```

Individual suites: `pytest tests/<group>/<suite>/ -v` — for example
`pytest tests/framework/bar_rendering/ -v`. Suites that run on real market data need the
[sample dataset](Readme.md#sample-data).

The runner leaves out the release-gate suites on purpose — the benchmark, the live adapters, the
live field study and the live signal feed: they need real credentials, and the live adapter suite
places real orders. How the runner finds suites and what it skips:
[Test Runner](docs/tests/tests_runner_docs.md).

---

## Visual Frontend (FiniexViewer)

To work on or use the FiniexViewer companion UI, see the setup guide:
→ [FiniexViewer Dev Setup](docs/user_guides/finiexviewer_setup.md)

---

## Data Behind Committed Files

Committed scenario sets and generator profiles — in `configs/` and in `tests/fixtures/` alike —
rest only on data the project's own collectors recorded, which is the data the project publishes.
Data from any other source, such as a venue's historical download, may be used on your own
machine, but no committed set or profile may read it or be derived from it: anyone working from
the project's published data would find nothing in that window, or derive different numbers.

---

## Pull Requests

- Branch from the current development branch — named after its version, `dev-v-<major>-<minor>`,
  the one open on GitHub — and target it with your PR, not `main`
- One logical change per PR
- Include a brief description of what changed and why
- All tests must pass before merge
