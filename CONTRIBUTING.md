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

### Several working trees on one machine

A second checkout or a `git worktree` lets two pieces of work run side by side. Three things keep
them apart:

- Place the worktree outside the repository's folder — the editor otherwise lists every file twice.
- Run Python in it with `PYTHONPATH` set to the worktree. The dev container points `PYTHONPATH` at
  the main checkout, and without the override a command started in the worktree imports the main
  checkout's code, silently.
- The data folders are tracked placeholders (`data/processed/`, `user_configs/`): keep them, and
  link the real content inside them, or the branch records them as deleted.

What runs is listed once per machine, not once per tree: `python python/cli/activity_cli.py list`
shows the work of every checkout, each with the tree it runs from (`~/.finiex/activities` holds the
list).

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
- **Old runs stay true** — a run writes down at its start what it uses, and nothing later rewrites
  that record from today's files; a change that gives runs a new input records it the same way —
  see [A run is a record, and an old one stays true](docs/introduction_to_the_ide.md#a-run-is-a-record-and-an-old-one-stays-true)

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
Generation warns when such data enters a profile or a set, and the test suite checks every set and
profile kept in those folders against the data on your machine.

---

## Pull Requests

- Branch from the current development branch — named after its version, `dev-v-<major>-<minor>`,
  the one open on GitHub — and target it with your PR, not `main`
- One logical change per PR
- Include a brief description of what changed and why
- All tests must pass before merge
