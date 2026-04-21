# FiniexViewer Setup — Visual Frontend

[FiniexViewer](https://github.com/dc-deal/FiniexViewer) is the visual companion for FiniexTestingIDE. It connects to the built-in HTTP API and provides a browser-based UI for data exploration and monitoring.

This guide covers setting up the dual-repo development environment so both projects run side-by-side in a single VS Code session.

---

## Prerequisites

- FiniexTestingIDE dev container running (the standard setup)
- Both repos cloned as **sibling directories** on the host:

```
.../code/
├── FiniexTestingIDE/    ← this repo
└── FiniexViewer/        ← companion repo
```

Clone FiniexViewer if you haven't already:

```bash
git clone https://github.com/dc-deal/FiniexViewer ../FiniexViewer
```

---

## Step 1 — Enable the Compose Extension

Open `.devcontainer/devcontainer.json` and change `dockerComposeFile` from a string to an array:

```jsonc
// Before
"dockerComposeFile": "../docker-compose.yml",

// After
"dockerComposeFile": [
    "../docker-compose.yml",
    "../docker-compose.finiexviewer.yml"
],
```

> **Do not commit this change.** It is a local override — each developer opts in manually.

---

## Step 2 — Rebuild the Dev Container

In VS Code: open the Command Palette (`Ctrl+Shift+P`) → **Dev Containers: Rebuild Container**.

After the rebuild, `/viewer` inside the container maps to `../FiniexViewer` on your host.

---

## Step 3 — Add FiniexViewer to the VS Code Workspace

To get full editor support (IntelliSense, search, file tree) for both repos in one window:

1. **File → Add Folder to Workspace…**
2. Select `/viewer` (inside the container)
3. Save the workspace file if prompted

Both repos are now visible in the Explorer panel.

---

## Step 4 — Start the API Server

In the FiniexTestingIDE terminal (inside the container):

```bash
python python/cli/api_server_cli.py --reload
```

Or use the VS Code launch entry **🚀 API Server (Dev)**.

The API is available at `http://localhost:8000`. OpenAPI UI: `http://localhost:8000/docs`.

---

## Phase 2 — Vite Dev Server (optional)

To also run the FiniexViewer Vite dev server in a dedicated container, activate the `viewer` profile from a **host terminal** (not inside the dev container):

```bash
cd FiniexTestingIDE
docker compose -f docker-compose.yml -f docker-compose.finiexviewer.yml --profile viewer up -d
```

The Vite dev server will be available at `http://localhost:5173`.

The container automatically installs npm dependencies on first start (`npm install && npm run dev`).

---

## Port Overview

| Port | Service |
|------|---------|
| `8000` | FiniexTestingIDE FastAPI (HTTP API) |
| `5173` | FiniexViewer Vite dev server |
