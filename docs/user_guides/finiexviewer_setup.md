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

The API is available at `http://localhost:8000`. OpenAPI UI: `http://localhost:8000/docs` — served only while no consumer token is configured, because the schema routes sit outside the prefix everything else is gated under (`docs/architecture/api_server_architecture.md`, *Schema surface*).

The base `docker-compose.yml` publishes that port as `127.0.0.1:8000:8000` — the operator's own
machine and nothing else. That is deliberate rather than cautious: the `reports` surface names
every run this installation has recorded, and the browser client's grant on it was issued against
exactly this reachability. Widening the bind means re-asking for those grants.

---

## Phase 2 — Vite Dev Server (optional)

To also run the FiniexViewer Vite dev server in a dedicated container, activate the `viewer` profile from a **host terminal** (not inside the dev container):

```bash
cd FiniexTestingIDE
docker compose -f docker-compose.yml -f docker-compose.finiexviewer.yml --profile viewer up -d
```

The Vite dev server will be available at `http://localhost:5173` — from this machine only, like the
API. A production build served with `vite preview` inside the container answers on
`http://localhost:4173`.

The container automatically installs npm dependencies on first start (`npm install && npm run dev`).

---

## A second server — reading an installation on another machine

The viewer can read more than one API server: the development installation beside it, and an
installation on another machine, such as a server that runs a bot. Each one is an installation of its
own, with its own data, its own tokens and its own contract. This section covers how to reach such a
server and tell it apart; how to run a bot there is
[Unattended Operation](../autotrader/unattended_operation.md).

- **Tell servers apart by `host_id`.** `GET /api/v1/health` serves it without a token, and the same
  value stands as `origin_host` on every activity row and run row. It stays when the address
  changes. `host_name` is a name for people to read, set in that installation's `api.host_name`, and
  null where nobody set one.
- **One token per server.** A token is known only to the installation that holds it in its
  `user_configs/credentials/inbound/consumer_tokens.json`. Mint it on that server with
  `api_token_cli.py account` and `mint`, and keep the plaintext on the viewer's side in its own
  gitignored workspace, never in a repository or a browser.
- **Read the contract per server**, from `X-Api-Contract`. Each installation is deployed on its
  own, so for a while one is ahead of the other.
- **Never publish that server's API port.** Bind it to the server's loopback (`127.0.0.1:8000`) and
  reach it through an SSH tunnel, with an account on the server that may open this one port and
  nothing else: no shell (`/usr/sbin/nologin`), its key restricted in `authorized_keys`, and a
  `Match User` block in `sshd_config` that repeats the limit:

  ```
  # ~tunnel-user/.ssh/authorized_keys
  restrict,port-forwarding,permitopen="127.0.0.1:8000" ssh-ed25519 AAAA… viewer tunnel

  # /etc/ssh/sshd_config.d/ — and the account added to AllowUsers
  Match User tunnel-user
      AllowTcpForwarding local
      PermitOpen 127.0.0.1:8000
      PermitTTY no
      X11Forwarding no
      AllowAgentForwarding no
      ForceCommand /usr/sbin/nologin
  ```

  The viewer's side keeps the tunnel open, in a loop so that it reconnects, and then reads
  `http://localhost:8010/api/v1` as that server:

  ```bash
  ssh -N -i <key> -o UserKnownHostsFile=<pinned known_hosts> -o StrictHostKeyChecking=yes \
      -o BatchMode=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
      -L 8010:127.0.0.1:8000 tunnel-user@<server>
  ```

  A tunnel that is down means no connection, never a statement about the server or a bot on it.
- **Beside a bot on the same machine**, run the API server in a container of its own and mount the
  bot's data home read-only, so a viewer can never change the bot's records. Two of its folders
  still need write access, because a read rebuilds an index there: `runs/ledger/`, and a folder of
  the API's own for the configuration directory's index (`data/runtime/config_directory/`). And
  start that container without an init process, so the API server is process 1 there: the
  activity list matches memory to activities by process number, and two containers can both have a
  process 8.

---

## Port Overview

| Port | Service | Published as |
|------|---------|---|
| `8000` | FiniexTestingIDE FastAPI (HTTP API) | `127.0.0.1:8000` — loopback only |
| `5173` | FiniexViewer Vite dev server | `127.0.0.1:5173` — loopback only |
| `4173` | FiniexViewer `vite preview` (the production bundle) | `127.0.0.1:4173` — loopback only |
| `8010` | a second server's API, through the SSH tunnel (the port is an example) | on the viewer's side only |

Both viewer ports are bound to the loopback for the API's reason: the Vite server forwards `/api`
to the API, so a viewer port open on every interface would make the API reachable from the network
through it.

The Vite container does NOT reach the API through that published port: it resolves `finiex-dev` on
the compose network (`VITE_API_BASE_URL`), which works regardless of what is published to the host.
