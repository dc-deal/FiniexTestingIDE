# Credentials Layout — Two Directions in One Folder

**Two kinds of secret live in `configs/credentials/`, and they have opposite handling rules.**
One is a registry of digests this application mints for other people; the other is plaintext that
other people minted for this application. Treating them alike is the mistake the folder invites,
because nothing about a flat list of `*.json` says which is which.

This document is the map. It does **not** explain how the HTTP API authenticates a request — that
is [`api_server_architecture.md`](api_server_architecture.md) — and it does not cover the
connection policy for reaching a peer, which is
[`external_connection_policy.md`](external_connection_policy.md).

---

## Which file is which

| File | Direction | Who mints it | What a loss costs | What disclosure costs |
|---|---|---|---|---|
| `consumer_tokens.json` | **inbound** — who may call us | we do | a consumer is locked out | nothing: it stores SHA-256 digests |
| `rag_credentials*.json` | **outbound** — a sibling project | they do | we cannot read their data | a third party reads as us |
| `collector_credentials*.json` | **outbound** — a sibling project | they do | we cannot read their archive | a third party reads as us |
| `kraken_credentials.json` | **outbound** — a trading venue | the venue | no live trading | **a third party can move money** |

Every file exists twice: a **tracked** copy under `configs/credentials/` whose secrets are empty
or switched off, and a **real** one under `user_configs/credentials/`, which is gitignored in
full. The tracked copy is the schema and the example; it is never the credential.

## Why the inbound file is not named after its protocol

It was called `api_tokens.json`, which says what protocol it serves and nothing about which way it
points. Renamed 2026-09-15 to `consumer_tokens.json` — the same word the code uses (`ConsumerToken`,
and the boot line that reports how many consumers are configured). A name travels with a file; a
folder only orders today's contents.

## The pairing: an address never travels alone

An outbound service's **address lives in the same block as the credential it belongs to**, with a
named environment and one switch that selects it. `sentiment_config.json::producer` is the shape:

```json
"producer": {
  "active": "dev",
  "endpoints": {
    "dev":        { "base_url": "http://host.docker.internal:8100",
                    "credentials_file": "rag_credentials_dev.json" },
    "production": { "base_url": "",
                    "credentials_file": "rag_credentials.json" }
  }
}
```

The pairing is what prevents the failure, not what documents it. A base URL stored apart from its
credential is how a development token reaches a production endpoint — and where the two
environments carry different grants, that arrives as a puzzling 403 on a route the caller believed
was allowed, rather than as an authentication error anyone would recognise.

The tracked file carries the development address, which is harmless, and leaves the production
address **empty**; the real one lives in `user_configs/`.

## The three directories

```
configs/credentials/
  inbound/   consumer_tokens.json                      who may call us
  peers/     rag_*, collector_*                        sibling projects, read-only
  venues/    kraken_credentials.json                   real money
```

Split on 2026-09-15, when the peer credentials reached four files. The pressure was recent: the
folder held a single file from March to August 2026, and four more arrived in the three weeks
before the split, each of them as "just one more file". That is how a flat namespace stops being
readable — not through one wrong decision, but through several right ones in a row.

A reader resolves a credential by joining a directory with a **relative path**, so the subdirectory
travels in the configured name (`peers/rag_credentials.json`) and the cascade
`user_configs/credentials/` → `configs/credentials/` needs no change.

**One thing the split nearly broke, recorded because it would have failed silently.** The guard that
refuses a real credential read from the tracked copy tested the *immediate* parent directory. Under
`configs/credentials/venues/…` that parent is `venues`, the test turned false, and the guard would
have stopped guarding — on the one file that can move money. It now matches `configs/credentials`
at any depth. A safety check that is keyed to a layout has to be re-read when the layout moves.

## Adding a peer

1. Ask the peer to mint a token per environment. Their live and development consumers usually carry
   **different grants**; keep them in separate files rather than one file with two values.
2. Write the real secrets to `user_configs/credentials/<peer>_credentials.json` and
   `<peer>_credentials_dev.json`. Never to the tracked copy.
3. Add an empty-valued placeholder of each to `configs/credentials/`, so the schema is visible to
   someone who has no secrets at all.
4. Give the peer its own config file with an `active` switch and an `endpoints` block pairing each
   `base_url` with its `credentials_file` — one file per peer, beside that peer's own timeouts and
   connection policy.
