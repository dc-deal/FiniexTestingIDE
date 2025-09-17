# 01 01 vision main

# 1. Vision & Ziele

## Vision

**FinexTestingIDE** ist eine IDE-artige Forschungs- und Testumgebung, die **Strategie-Code**, **Marktdaten** und *
*Metriken** in einem sauber orchestrierten Flow zusammenführt. Ziel ist es, Handelsforschung **reproduzierbar**, *
*ehrlich vergleichbar** und **operativ verwertbar** zu machen—vom ersten Prototyp bis zum robusten, dokumentierten
Ergebnis.  
Kernprinzip ist **radikale Transparenz dort, wo sie nützt**, und **strikte Kapselung dort, wo geistiges Eigentum
geschützt werden muss**. Jede Annahme (Gebühren, Slippage, Zeitzonen, Seeds), jeder Datensatz (Provenance, Schema,
Hashes) und jeder Run (Konfiguration, Artefakte, Logs, Berichte) ist versioniert und automatisiert. Gleichzeitig erlaubt
eine **Strategie-Blackbox-API** die Nutzung proprietärer Strategien ohne Offenlegung der Interna.

## Besonderheit: Strategie-Blackbox-API

Die **Blackbox-API** ermöglicht es, Strategien als **undurchsichtige, aber testbare** Module zu betreiben.  
Was „hinter“ der Blackbox passiert, ist beliebig (z. B. ein sehr einfacher Envelope-/Trend-Algorithmus)—entscheidend ist
nur, dass die Blackbox einen **kleinen, stabilen Vertrag** erfüllt. Dadurch kann die Testing-IDE die Strategie *
*vollumfänglich testen**, ohne Interna zu kennen.

### Warum das stark ist

- **IP-Schutz:** Interna bleiben im Team/bei Partner:innen oder vendor-seitig geschützt.
- **Fairer Vergleich:** Tests, Metriken und Reports sind identisch—Blackbox vs. „offene“ Strategien.
- **Debug optional:** Die Blackbox **kann** Diagnose-Metadaten liefern (Parameter, Indikatorstände), **muss** es aber
  nicht. Ein **Prod-Modus** unterbindet jegliche Meta-Ausgabe.
- **Nahtlose Übergabe:** Dieselbe Blackbox kann später **unverändert** an den **FINEXautotrader** übergeben werden, der
  dann handelt (siehe *Handover*).

### Minimaler Vertrag (I/O)

Die Testing-IDE garantiert nur diese Schnittstellen—keine Reflexion/Inspektion der Interna.

**Eingaben (vom Runner/IDE an die Blackbox):**

```json
{
  "type": "bar",
  "ts": "2020-01-01T00:00:00Z",
  "symbol": "XBTUSD",
  "open": 100.0,
  "high": 101.2,
  "low": 99.8,
  "close": 100.7,
  "vol": 12.3,
  "env": {
    "fees_bps": 1.5,
    "slippage_bps": 2.0,
    "seed": 42,
    "mode": "debug|prod"
  }
}
```

**Ausgaben (von der Blackbox an Runner/IDE):**

```json
{
  "signal": "BUY|SELL|FLAT",
  "qty": 0.2,
  "price": null,
  "risk": {
    "sl": 98.9,
    "tp": 102.1
  },
  "meta": {
    "indicators": {
      "env_up": 1.2,
      "env_dn": 0.8,
      "trend": "up"
    },
    "params": {
      "env_len": 20,
      "env_dev": 1.5
    }
  }
}
```

**Lebenszyklus-Hooks (Beispiele):**

- `on_init(config)` – erhält Start-Konfiguration (Params, Symbols, Zeitrahmen)
- `on_event(bar|tick)` – liefert Signale/Orders gemäß Vertrag
- `on_stop()` – Cleanup, optionaler Abschluss-Report (nur Debug)

> **Prod-Modus:** `meta` wird komplett unterdrückt (leeres Objekt oder Feld fehlt).  
> **Determinismus:** Die Blackbox nutzt *nur* den übergebenen `seed` für PRNG-Operationen.

### Konzeptbeispiel: Envelope + Trend als Blackbox

> **Hinweis:** Interna werden **nicht** offengelegt—dies dient nur der Verständlichkeit des Konzepts.

- **Envelope-Filter:** Bollinger/Envelope-Band basierend auf `close` mit `env_len`, `env_dev`.
- **Trend-Gate:** Trade nur, wenn `trend ∈ {up, down}` stabil ist (z. B. EMA-Kreuz).
- **Signal-Logik:**
    - Bei **up-Trend** und `close` nahe unterem Envelope → **BUY** (Mean Revert in Trendrichtung).
    - Bei **down-Trend** und `close` nahe oberem Envelope → **SELL**.
- **Risk Controls:** SL/TP aus ATR/Prozent, max. Risiko pro Trade, Tagesverlust-Limit.
- **Debug an (IDE-Tests):** `meta.indicators` & `meta.params` gefüllt.
- **Prod an (Handover):** `meta` leer; nur `signal/qty/price/risk`.

### Handover zu **FINEXautotrader**

- **Gleicher Vertrag**: FINEXautotrader konsumiert dieselben `signal`-Events (BUY/SELL/FLAT, qty, optional price, risk).
- **Adapter-Layer**: nur Transport/Execution ändert sich (z. B. REST/WS → Broker-API).
- **Konfig-Wiederverwendung**: `config.json`/`params.json` der IDE-Runs werden 1:1 im Autotrader referenziert.
- **Betriebsmodi**:
    - *Paper*: Autotrader spiegelt Execution ohne Live-Kapital.
    - *Live*: Prod-Modus, **keine** `meta`-Ausgabe, Audit-Logs aktiv.

## Ziele

- **Schnelle Iterationen:** Idee → Blackbox-Build → Test → Report in **Minuten**.
- **Determinismus & Vergleichbarkeit:** Fixe Seeds, explizite Annahmen (Fees/Slippage/Resampling), standardisierte
  Metriken (CAGR, σ, Sharpe, Sortino, MaxDD, MAR, MAE/MFE).
- **Automatisierte Artefakte:** `config.json`, `metrics.json`, `equity_curve.parquet`, `trades.csv`, `report.html`,
  `logs.ndjson` pro Run—bereitgestellt durch CI/Runner.
- **Standardisierte Schnittstellen:** Die **Blackbox-API** ist minimal, stabil, sprach-agnostisch (Python, MQL5-Bridge,
  andere Sprachen via gRPC/WS/STDIO möglich).
- **Sichere Betriebsmodi:** Debug-Infos zwecks Diagnose **an** (Test), **aus** (Prod/Live).
- **Nahtloses Deployment:** Identische Artefakte und Parameter ermöglichen die **spätere Übergabe an FINEXautotrader**
  ohne Code-Änderung an der Strategie.

## Nicht-Ziele (Abgrenzung)

- Kein offener Zugriff auf Blackbox-Interna (keine Pflicht zur Offenlegung, keine erzwungene Telemetrie).
- Kein Auto-Trading „out of the box“ in der IDE—der Live-Handel ist Aufgabe des **FINEXautotraders**.
- Kein HFT/Ultra-Low-Latency im MVP (spätere Optimierungen möglich).

## Messbare Kriterien (Auszug)

- **Time-to-First-Backtest** (Blackbox): < **30 min** inkl. Daten, Run, Report.
- **Determinismusquote**: ≥ **99 %** identische Ergebnisse bei Wiederholung mit gleichem Seed.
- **„Leak-Free Prod“**: In Prod-Runs ist `meta` garantiert **leer** bzw. nicht vorhanden.
- **Handover-Fähigkeit**: Ein Blackbox-Artefakt, das IDE-Tests besteht, ist **ohne Änderung** im FINEXautotrader
  lauffähig (Paper-/Live-Modus über Adapter).


---

# 01 02 vision skalierung


# Skalierung & Parallelisierung (1000+ Szenarien / mehrere Währungspaare)

**Zielbild:** FinexTestingIDE kann **>1000 Szenarien gleichzeitig** ausführen—über **mehrere Währungspaare**,
Zeitfenster und Parameterkombinationen hinweg—unter Ausnutzung von **Multi-Core/Multiprocessing** (lokal) und optional *
*verteilten Workern** (Cluster). Die Ergebnisse bleiben **deterministisch** und **vergleichbar**, egal in welcher
Reihenfolge oder auf wie vielen Cores/Nodes die Runs gelaufen sind.

## Prinzipien

- **Embarrassingly Parallel:** Jeder Szenario-Run ist unabhängig (keine geteilten, mutierbaren Zustände).
- **Deterministische Parallelität:** Ein **Master-Seed** erzeugt pro Run einen **abgeleiteten Seed** (
  `seed_i = H(master_seed, run_id)`), sodass Scheduling/Parallelität keine Auswirkung auf das Ergebnis hat.
- **Skalierung nach Bedarf:** Lokal **Multi-Core (Prozesse)**; optional **distributed** via Queue/Worker (z. B.
  Redis/Kafka).
- **Ressourcen-Schranken:** Konfigurierbare **Max-Concurrency**, **RAM-Limits**, **I/O-Budgets**; **Work-Stealing**
  balanciert lange/kurze Runs.
- **Daten-Lokalisierung & Caching:** Sharding/Chunking von Datensätzen, **Memory-Mapping**/On-Demand-Streaming, um
  RAM-Peaks zu vermeiden.

## Fan-Out/Fan-In (Testmatrix)

Die IDE zerlegt die Testmatrix in atomare Runs:

```
Szenarien = Instrumente × Zeitfenster × Parameter-Sets × (optional) Walk-Forward-Splits
```

Beispiel (FX-Paare): `EURUSD, GBPUSD, USDJPY, AUDUSD, USDCHF, USDCAD, NZDUSD, ...`  
Mit 20 Parametern (Grid/Random), 3 Zeitfenstern und 10+ Paaren sind **>1000 Runs** die Norm, nicht die Ausnahme.

## Lokale Parallelität (Multi-Core)

- **Prozess-Pool** (CPU-bound) nutzt alle Cores ohne GIL-Engpass.
- **Thread-Pools** nur für I/O-lastige Schritte (Daten-Fetch, Kompression, Upload).
- Pins/Affinität (optional): „gleiche Instrumente → gleicher Worker“ für warmen Cache.

## Verteilte Ausführung (optional)

- **Broker/Queue** (Redis Streams/Kafka) verteilt Jobs an Worker-Knoten.
- **Idempotenz & Resume:** Checkpoints/Artefakte pro Run; **Retry** ohne doppelte Trades/Dateien.
- **Horizontale Skalierung:** weitere Worker-Pods hinzufügen, keine Code-Änderung nötig.

## Konfigurationsbeispiel

```yaml
runner:
  concurrency:
    max_workers: 16           # Anzahl paralleler Prozesse (lokal)
    strategy: process         # process | thread | distributed
  distributed:
    enabled: false
    broker: redis://localhost:6379/0
    topic: finex.runs

matrix:
  instruments: [ EURUSD, GBPUSD, USDJPY, AUDUSD, USDCHF, USDCAD, NZDUSD, BTCUSD ]
  windows:
    - { start: 2018-01-01, end: 2019-12-31 }
    - { start: 2020-01-01, end: 2021-12-31 }
    - { start: 2022-01-01, end: 2023-12-31 }
  params:
    env_len: [ 10, 20, 30, 40, 50 ]
    env_dev: [ 1.0, 1.5, 2.0 ]
    trend_len: [ 50, 100 ]
  target_runs: 1000

determinism:
  master_seed: 1337
  seed_derivation: "hash(master_seed, run_id)"  # deterministische Ableitung

io:
  dataset_cache: .cache/datasets
  mmap: true
  prefetch: 2
  artifacts_store: s3://finex-artifacts/{run_id}/
```

## Qualitätsgarantien

- **Gleiche Inputs + gleicher Seed ⇒ gleiche Outputs**, unabhängig von Core-Zahl/Worker-Reihenfolge.
- **Artefakte pro Run** (config/metrics/equity/trades/report/logs) sichern **Vergleichbarkeit** und **Resume-Fähigkeit
  **.
- **Fairness & Stabilität:** Work-Stealing, Retry mit Backoff, Quotas für CPU/RAM/I/O.

## Bezug zur Blackbox-API

Die **Strategie-Blackbox-API** bleibt unverändert: Jeder Run instanziiert eine Blackbox-Instanz mit **deriviertem Seed
**. Debug-Metadaten sind **konfigurierbar** (an/aus). So können **proprietäre** Strategien in **tausenden parallelen
Runs** getestet werden, ohne Interna offenzulegen; späterer **Handover** an **FINEXautotrader** ist 1:1 möglich.

---

**Kurzfassung:** Ja—FinexTestingIDE unterstützt **gleichzeitiges Testen von 1000+ Szenarien** über **mehrere
Währungspaare** hinweg durch **Multi-Core-Parallelität** (lokal) und optional **verteilte Worker**—bei **voller
Reproduzierbarkeit**.


---

# 01 03 vision blackbox

## Blackbox-Integration: Startdaten & High-Performance-Zuführung

> **Kernaussage:** Die Blackbox muss **vor** dem ersten Event (Start-Tick/-Bar) eine ausreichende **Historie**
> besitzen (Warm-up), um Indikatoren/Features korrekt zu berechnen. Die FinexTestingIDE stellt diese Startdaten *
*deterministisch**, **schnell** und **ressourcenschonend** bereit—für **1000+ parallele Szenarien** über mehrere
> Währungspaare hinweg.

### Anforderungen (aus Sicht der Blackbox)

- **Vollständige Historie bis zum Startzeitpunkt** (z. B. `N_warmup` Bars/Ticks), bestimmt durch den längsten Lookback (
  Envelope/EMA/ATR/…).
- **Deterministische Bereitstellung:** Für denselben Run (Seed/Datensatz-Hash) stets **identische** Startmenge.
- **Read-only, niedrige Latenz:** **Zero-copy/Memory-mapped** Zugriffe, **keine** unnötigen
  Kopien/JSON-Deserialisierung.
- **Skalierbarkeit:** Versorgung von **1000+** parallel laufenden Szenarien (mehrere Paare, Zeitfenster, Parameter-Sets)
  ohne RAM-Explosion.

### Designoptionen zur Startdaten-Provisionierung

1. **Gemeinsame Quelldatenbank (empfohlen)**
    - **Ablage:** Spaltenorientiert (Parquet/Arrow) je *Instrument × Timeframe*, versioniert mit **Dataset-Hash** (z. B.
      `datasets/{hash}/FX/EURUSD/M1/*.parquet`).
    - **Zuführung:** **Memory-mapping** + OS-Page-Cache ⇒ **zero-copy Views** in Worker-Prozessen; optional **Arrow
      Flight**/**lokaler Data-Server** für Cluster.
    - **Scheduling:** **Data-Locality** (Runs mit gleichen Instrumenten bevorzugt auf denselben Worker) für warmen
      Cache.
    - **Determinismus:** Der **Snapshot** (Zeitfenster + Hash) wird beim Run **eingefroren** (kein „latest“).

2. **Großes JSON beim Startup (nicht empfohlen)**
    - **Nachteile:** Hoher Parse-Overhead, RAM-Duplikate je Thread/Prozess, keine OS-Cache-Wiederverwendung, träge
      Startzeiten, fehleranfällig bei 1000+ Runs.
    - **Allenfalls** für Mikrotests/Unit-Tests mit winzigen Fenstern geeignet.

3. **Hybrid**
    - **Start-Snapshot** + **On-Demand History API** (nur aus demselben Snapshot), falls die Blackbox in `on_init()`
      weitere Spalten/Lookbacks anfordert.
    - **Snapshot-Konsens** garantiert (keine Mischung verschiedener Versionen).

### Empfohlener Ansatz (IDE/Runner/Worker)

**A. Snapshottierung & Provisionierung**

1. **Snapshot planen:** Strategy-Tester berechnet **exakten Startzeitpunkt** und **Warm-up** (`N_warmup`) aus längstem
   Lookback (z. B. `env_len`, `trend_len`, `atr_len`) + Sicherheitsaufschlag (z. B. +10 %).
2. **Selektiv laden:** Bars/Ticks als **Parquet/Arrow** mit **Predicate Pushdown**; **Memory-mapping** aktivieren.
3. **Handles statt Kopien:** Worker/Blackbox erhält **Handles/Views** (Pfad/FD/Offsets), keine massiven Payloads.

**B. API-Vertrag in `on_init()` (Data-Provider statt JSON-Blob)**

```python
# Pseudocode
hist = ctx.data.history(
    symbol="EURUSD",
    frame="M1",
    end_ts=config.start_ts,      # exklusiv
    lookback_bars=required_bars  # auto ermittelt oder explizit
)
# hist = zero-copy View (Arrow/Polars/NumPy) auf mmap-Datei, read-only
```

- **Auto-Warm-up:** Falls die Blackbox mehr als bereitgestellt benötigt → `ctx.require_history(min_bars)` ⇒ Tester *
  *rescheduled** deterministisch mit erweitertem Snapshot.

**C. Parallelität & Performance**

- **Prozess-basiert** (CPU-bound) ⇒ GIL umgehen; Threads nur für I/O (Fetch/Kompression/Upload).
- **Data Locality:** Szenarien mit gleichen Instrumenten/Frames gruppiert; weniger neue mmaps.
- **I/O-Strategien:** Sequential Reads (OS-Hints), **Readahead**, **Prefetch-Queues**, **chunked scans**.
- **Ressourcenlimits:** Max-Concurrency, RAM- und I/O-Budgets; **Work-Stealing** gegen Laufzeitstreuung.

**D. Determinismus**

- **Fixierter Dataset-Hash**, normierte **Zeitzone**/**Rundungsregeln**.
- **Master-Seed** ⇒ pro Run abgeleiteter Seed (`seed_i = H(master_seed, run_id)`).
- Thread/Worker-Reihenfolge beeinflusst **nicht** das Ergebnis.

**E. Robustheit**

- **Idempotente Runs:** Artefakte pro Run-ID (`config.json`, `metrics.json`, `equity_curve.parquet`, `trades.csv`,
  `report.html`, `logs.ndjson`).
- **Resume/Retry:** Gleicher Snapshot/Offsets, keine Doppel-Trades/Dateien.
- **Extend & Reschedule:** Bei unvollständigen Snapshots automatische Erweiterung mit Audit-Trail.

### Bezug zur Strategie-Blackbox-API & FINEXautotrader

- **Gleicher Event-Vertrag** in IDE und Autotrader (Signals: `BUY|SELL|FLAT`, `qty`, optional `price`, `risk`).
- **Prod-Modus:** Keine `meta`-Leaks; Debug-Infos nur in Tests.
- **Handover 1:1:** Identische Blackbox kann nach bestandenem IDE-Test im **FINEXautotrader** (Paper/Live) ohne
  Code-Änderung laufen; lediglich der Data-Provider wechselt von Snapshot/History auf Live-Feed mit Rolling-Cache.

### Beispiel-Konfiguration

```yaml
data_provider:
  kind: snapshot
  format: parquet
  root: datasets/{dataset_hash}
  memory_map: true
  cache:
    local_dir: .cache/datasets
    readahead: 8
    mmap_hugepages: false

warmup:
  policy: auto            # auto | fixed
  min_bars: 2000          # fallback, falls Blackbox nichts meldet
  safety_margin: 0.10     # +10% auf längsten Lookback

runner:
  concurrency:
    strategy: process     # process | distributed
    max_workers: 16
  locality:
    group_by: [ "symbol","frame" ]

determinism:
  master_seed: 1337
  seed_derivation: "hash(master_seed, run_id)"
```

### Fazit

- **Columnar Snapshot + mmap + Views** statt riesiger JSON-Payloads.
- **Data-Provider-API** im `on_init()` liefert **zero-copy History**.
- **Auto-Warm-up/Reschedule** für verlässliche Indikator-Initialisierung.
- **Data Locality + Prozess-Parallelität** für 1000+ Szenarien.
- **Strenger Determinismus** (Hash/Seeds/Zeitzonen) für reproduzierbare Ergebnisse und **sauberen Handover** an den
  FINEXautotrader.


---

# 01 04 saas integration

# Cloud & SaaS‑Vision — **FINEXplatform**

> **Zielbild:** Die FINEXplatform stellt **FinexTestingIDE** als **Multi‑Tenant SaaS** bereit. Nutzer:innen buchen **Rechenleistung für Tests** on‑demand. Abgerechnet wird über ein **leistungsbasiertes Token‑System** (tägliche Token‑Budgets, Pay‑as‑you‑go, Reservierungen). Der Service ist **sicher**, **reproduzierbar**, **skalierbar** und **compliant** (EU‑Datenhaltung, DSGVO).

---

## 1) Wertversprechen
- **Sofort loslegen:** Kein Setup, keine Serverpflege – Browser auf, Strategien testen, Reports teilen.
- **Skalierung nach Bedarf:** Von einzelnen Runs bis **1000+ Szenarien parallel** (Multi‑Core/Worker‑Cluster).
- **Reproduzierbarkeit:** Fixierte Datasets/Snapshots, deterministische Seeds, versionierte Artefakte.
- **Kostenkontrolle:** Token‑Budget pro Tag/Projekt, harte Limits, Budget‑Alarme, Preemptible‑Rabatte.
- **Sicherheit & Compliance:** Mandantentrennung, Verschlüsselung, Audit‑Logs, EU‑Data Residency.

---

## 2) Architektur (Überblick)
```mermaid
flowchart LR
  subgraph ControlPlane
    IAM[OIDC/SSO & RBAC]
    Orchestrator[Run Orchestrator]
    Billing[Metering & Billing]
    Catalog[Dataset Registry]
    Admin[Org/Project Admin]
  end

  subgraph DataPlane
    Queue[(Jobs Queue)]
    W1[Worker Pool A]
    W2[Worker Pool B]
    S3[(Artifacts Store)]
    DB[(Runs/Meta DB)]
    Obs[Telemetry: Logs/Metrics/Traces]
  end

  FE[Web App (IDE)] -->|REST/WS| Orchestrator
  Orchestrator --> Queue
  Queue --> W1 & W2
  W1 & W2 --> S3 & DB
  W1 & W2 --> Obs
  Orchestrator --> Billing
  Orchestrator --> Catalog
  FE --> IAM
```
**Kernelemente**
- **Control‑Plane:** Auth/SSO, Orchestrierung, Abrechnung, Datenkatalog, Admin‑UIs.
- **Data‑Plane:** Isolation pro Tenant/Projekt, elastische Worker‑Pools, Artifact‑/Meta‑Speicher, Observability.
- **Data Residency:** Mandanten können **EU‑Region** wählen (Standard), weitere Regionen optional.

---

## 3) Multi‑Tenancy & Sicherheit
- **Isolation:** Logische Isolation (Org → Projekt → Space); optionale **dedizierte Worker‑Pools** für hohe Anforderungen.
- **Verschlüsselung:** TLS in Transit, **KMS‑gestützt** at Rest (Artefakte, Metadaten); Tenant‑spezifische Keys.
- **Secrets:** OIDC‑gebundener Zugriff; Build‑/Run‑Secrets nie im Klartext gespeichert.
- **Audits:** Unveränderbare **Audit‑Logs** (Run‑Lifecycle, Artefakt‑Zugriffe, Admin‑Aktionen).

---

## 4) Token‑basiertes Abrechnungsmodell

### 4.1 Compute‑Einheit (CU)
Um unterschiedliche Workloads fair abzubilden, wird die **Compute Unit (CU)** als gewichtete Mischung gemessen:
```
CU = vCPU_min
   + α · RAM_GB_min
   + β · IO_GB                                 # Lesen/Schreiben von/zu Storage
   + γ · GPU_min                                # falls GPU‑Jobs
```
- Default‑Gewichte (Startwerte): `α=0.25`, `β=0.05`, `γ=5.0`  
- 1 **Token** deckt z. B. **1 CU** ab (feinere Preisstaffel je Plan).

### 4.2 Token‑Mechanik
- **Daily Drip:** Jeder Plan erhält **täglich** neue Tokens (Mitternacht UTC oder lokale Orga‑Zeitzone).
- **Bucket‑Regeln:** Max‑Kapazität (z. B. 5× Daily Drip), **kein negatives** Saldo (außer explizitem Überziehungskorridor).
- **Verbrauch:** Während ein Run läuft, wird **minütlich** der aktuelle **CU‑Verbrauch** verbucht (genau‑einmal‑Semantik).
- **Reservierungen/Commit:** Vergünstigte Tokens bei Monats‑Commit (Reservierte vCPU‑Minuten/Worker‑Slots).  
- **Preemptible‑Discount:** Bis zu −70 % bei „unterbrechbaren“ Jobs (automatischer Resume).

### 4.3 Preislogik (Beispiele, fiktiv)
- **Free/Trial:** 50 Tokens/Tag, Bucket‑Cap 100, Preemptible‑only.
- **Pro:** 1 000 Tokens/Tag, Bucket‑Cap 5 000, Standard‑Worker, E‑Mail Support.
- **Team:** 5 000 Tokens/Tag, Bucket‑Cap 25 000, dedizierbare Pools, SSO, Prioritätssupport.
- **Enterprise:** Custom, **dedizierte Cluster**, BYOK‑Verschlüsselung, DPA/SLA individuell.

> **Hinweis:** Endpreise, Steuer/Region und exakte Gewichte werden in der Pricing‑Policy festgelegt und regelmäßig überprüft.

---

## 5) Metering, Billing & Wallet

### Ereignisse (Metering‑Stream)
- `run.queued` → Ticket/Run‑ID erzeugt (ohne Kosten)
- `run.started` → Taktung beginnt (pro Minute CU‑Messung)
- `run.progress` → periodische Verbrauchs‑Events (Aggregationsfreundlich)
- `run.completed|failed|stopped` → Taktung endet, Schlussbuchung

### Aggregation & Abrechnung
1. **Ingestion:** Ereignisse in eine **append‑only** Pipeline (idempotente Upserts).  
2. **Aggregation:** Rolling‑Fenster (1 min/5 min) → Tages‑/Projektsummen.  
3. **Buchung:** Abgleich mit Wallet/Bucket, **exactly‑once** Verbrauch pro Run‑ID.  
4. **Rechnung:** Monatliche Abrechnung (Invoices, Export als PDF/CSV), Webhooks für ERP.

### Wallet‑Funktionen
- **Top‑up** (Karte/SEPA), **Guthaben‑Alarm**, **Auto‑Stop** bei Limit, **Budget‑Caps** pro Projekt.
- **Tags/Kostenstellen**: Runs können Kostenstellen zugeordnet werden (Reporting).

---

## 6) Nutzer‑Erlebnis (UX‑Flows)
- **Balance & Forecast:** Token‑Kontostand, Tagesprognose, aktive Runs, Kostenstellen‑Breakdown.
- **Budget‑Policies:** Hard/Soft‑Limits (Stop/Warnung), Zeitfenster (z. B. 08–20 Uhr), Preemptible‑Opt‑in.
- **Run‑Planung:** Matrix‑Runs mit gezeigter **Kosten‑Prognose** und „Spar‑Optionen“ (Preemptible, Night‑Slots).
- **Alerts:** E‑Mail/Slack/Teams‑Benachrichtigungen bei 50 %/80 %/100 % Budget.

---

## 7) Daten & Compliance
- **Data Residency:** Standard **EU** (weitere Regionen optional).  
- **DSGVO/DPA:** Auftragsverarbeitung, Vertragsanlagen (TOMs), Löschkonzepte (Right‑to‑erasure).  
- **Retention:** Artefakte standardmäßig 30/90 Tage (Plan‑abhängig), **immutable** Option für Audits.  
- **Exports:** Vollständiger Artefakt‑/Metrik‑Export (S3‑Kompatibel, API/CLI).

---

## 8) Zuverlässigkeit & SLOs
- **SLOs (Startwerte):** API Uptime 99.9 %, Orchestrator 99.9 %, Storage 99.95 %.  
- **Resilience:** Mehrzonen‑Storage, Retry‑Strategien, **Preemptible Resume**, Quotas.  
- **Status & Incidents:** Öffentliche **Status‑Seite**, Post‑Mortems, RCA‑Transparenz.

---

## 9) Anti‑Abuse & Fairness
- **Rate‑Limits & Quotas** pro Tenant/Token‑Bucket.  
- **Anomalie‑Erkennung:** Ungewöhnliche IO/CPU‑Spitzen → Throttling/Flagging.  
- **Missbrauchsschutz:** Kreditkarten‑/Bot‑Abuse‑Filter, Preemptible‑Beschränkungen.  
- **IP‑Schutz:** Blackbox‑Prod‑Modus verhindert Metadaten‑Leaks (kein `meta`).

---

## 10) Roadmap (SaaS‑Erweiterungen)
- **Marketplace (optional):** Kuratierte Datensätze/Strategie‑Vorlagen (mit Revenue‑Share).  
- **Spot/Reserved Worker Pools:** Benutzerdefinierte Preis‑/Leistungsprofile.  
- **Org‑weite Policies:** Budget‑Zeitpläne, Regionen‑Pinning, BYOK‑KMS.  
- **On‑Prem/Air‑gapped Edition:** Gleiche APIs, selbst verwaltete Infrastruktur.

---

## 11) Beispiel‑Konfiguration (Token & Pläne)
```yaml
billing:
  units:
    weights:
      cpu_vcpu_min: 1.0     # 1 CU pro vCPU‑Minute
      ram_gb_min: 0.25      # α
      io_gb: 0.05           # β
      gpu_min: 5.0          # γ
  tokens:
    bucket_cap_multiplier: 5     # Max‑Cap = 5 × daily_drip
    prices:
      cu_per_token: 1.0          # 1 Token = 1 CU
plans:
  trial:
    daily_drip: 50
    preemptible_only: true
  pro:
    daily_drip: 1000
    preemptible_discount: 0.7     # 70 % günstiger
  team:
    daily_drip: 5000
    dedicated_pools: optional
    sso: true
  enterprise:
    daily_drip: custom
    dedicated_clusters: true
    byok_kms: true
    dpa_sla_custom: true
runner:
  scheduling:
    preemptible: opt_in
    max_concurrency: 64
  determinism:
    master_seed: 1337
    seed_derivation: "hash(master_seed, run_id)"
data:
  residency: EU
  retention_days: 90
  export_s3_compatible: true
```

---

## 12) Zusammenfassung
Die FINEXplatform als SaaS liefert **elastische Compute‑Kapazität** für FinexTestingIDE, **abrechenbar über Tokens**, mit **harter Kostenkontrolle**, **strikter Reproduzierbarkeit** und **Enterprise‑tauglicher Sicherheit/Compliance**. Der Token‑Ansatz erlaubt **feine Fairness** (CPU/RAM/IO/GPU‑Gewichte), während Preemptible‑Jobs und Reservierungen **signifikant sparen** – ohne die wissenschaftliche Qualität (Determinismus, Artefakte, Auditierbarkeit) zu kompromittieren.


---

# 02 prinzipien

# 2. Produktprinzipien & Nicht‑Ziele

**Transparenz, Reproduzierbarkeit, Automatisierung, Modularität, Pragmatismus**

**Nicht‑Ziele:** Kein Auto‑Trading‑Bot, keine proprietären Daten.


---

# 03 personas usecases

# 3. Personas & Use‑Cases

- Researcher, Data Engineer, DevOps, Stakeholder

Use‑Cases: Quick Backtest, Parameter‑Sweep, Walk‑Forward, Regression, Portfolio‑Vergleich


---

# 04 scope

# 4. Produktumfang (MVP → v1)

**MVP:** Python SDK, deterministische Engine, CSV/Parquet, CLI, Grundmetriken, CI

**v1:** Web‑IDE, Sweeps UI, Paper‑Broker, Event‑Bus, OTel


---

# 05 architektur

# 5. Architektur — Überblick & Diagramme

```mermaid
flowchart LR
  subgraph Frontend [Web‑IDE (Vue 3 + Monaco)]
    E[Editor] --> R[Run Console]
    R --> X[Results Explorer]
  end
  subgraph API [FastAPI REST/WS]
    A1[/POST /runs/] --> Q[(Queue)]
    WS[(WebSocket)] -->|run.progress| FE[Frontend]
  end
  subgraph Engine [Backtesting‑Engine & Pipelines]
    Q --> W[Worker]
    W --> S3[(S3/MinIO)]
    W --> DB[(PostgreSQL)]
    W --> MET[(Metrics Exporter)]
  end
  FE -->|REST/WS| API
  API --> Engine
```


---

# 06 datenmodell schemata

# 6. Datenmodell, Schemata & Artefakte

**Entitäten:** Instrument, Bar, Tick, Strategy, Run, Order, Trade, Position, Metrics, Report

**Run JSON‑Schema (Ausschnitt)**
```json
{
  "$schema": "https://json-schema.org/draft/2020-12/schema",
  "title": "Run",
  "type": "object",
  "properties": {
    "id": {"type": "string"},
    "strategy": {"type": "string"},
    "params": {"type": "object"},
    "dataset_ref": {"type": "string"},
    "seed": {"type": "integer"},
    "fees_bps": {"type": "number"},
    "slippage_bps": {"type": "number"},
    "status": {"enum": ["queued","running","failed","completed"]}
  },
  "required": ["id","strategy","dataset_ref","seed","status"]
}
```

**Artefakte**
```
artifacts/
  runs/{{run_id}}/
    config.json
    equity_curve.parquet
    trades.csv
    metrics.json
    report.html
    logs.ndjson
```


---

# 07 strategy sdk

# 7. Strategy‑SDK (Python) & MQL5‑Bridge

Lifecycle: on_start, on_bar, on_tick, on_stop

Kontrakte: ctx.buy/sell/close_all, ctx.position/orders/pnl, indicators

**Beispiel (Pseudo):**
```python
class MeanReversion:
    params = {"fast": 10, "slow": 30, "risk_per_trade": 0.01}
    def on_start(self, ctx): pass
    def on_bar(self, ctx, bar): pass
```

MQL5‑Bridge: JSON Events, Replay‑Service


---

# 08 datasources ingestion

# 8. Datenquellen, Ingestion & Katalogisierung

Adapter: CSV/Parquet, HTTP/REST (CCXT)

Qualität: UTC, Dedupe, Missing‑Values, Schema‑Checks, Hashes

Katalog: datasets.yaml mit Quelle/Lizenz/Schemata


---

# 09 backtests sweeps

# 9. Backtests, Parameter‑Sweeps & Walk‑Forward

Backtests: Bars, optionale Tick‑Interp., Fees/Slippage

Sweeps: Grid, Random → Heatmaps/Pareto

Walk‑Forward: Rolling Train/Test, Stabilität


---

# 10 metriken formeln

# 10. Metriken, Kennzahlen & Formeln

CAGR, Volatilität, Sharpe, Sortino, MaxDD, MAR; Win‑Rate, Payoff, MAE/MFE

**Drawdown Pseudocode**
```python
peak = -1e9; max_dd = 0
for v in equity_curve:
    peak = max(peak, v)
    dd = (peak - v) / peak
    max_dd = max(max_dd, dd)
```


---

# 11 reporting

# 11. Reporting, Dashboards & Exporte

HTML interaktiv, CSV/JSON, PNG; Equity, Drawdown, Trade‑Dist, Heatmaps

Vergleich: mehrere Runs nebeneinander; Auto‑Report in CI


---

# 12 api

# 12. API (REST/WebSocket) — OpenAPI

Siehe `api/openapi.yaml` für Endpunkte/Schemata.


---

# 13 ux ide

# 13. Web‑IDE & UX‑Flows

Ansichten: Editor, Runner, Results, Datasets, Sweeps

Flows: New Strategy → Run → Compare; Datasets -> Validate -> Use


---

# 14 qa

# 14. Qualitätssicherung & Teststrategien

Unit‑Tests, Property‑Based, Golden‑Files, Smoke/Load


---

# 15 security compliance

# 15. Sicherheit, Secrets, Compliance (DSGVO)

Least Privilege, Audit‑Logs, PII‑Minimierung, Lizenzfelder, SCA


---

# 16 betrieb monitoring

# 16. Betrieb, Monitoring & Observability

Logs (NDJSON), Prometheus, Grafana, OTel, Alerts


---

# 17 setup local

# 17. DevEx: Lokales Setup & Onboarding

Python 3.11+, Node 20+, Docker Desktop

```bash
pip install numpy pandas pyarrow polars pydantic fastapi uvicorn
```


---

# 18 ci cd

# 18. DevOps, CI/CD & Releaseflow

Branching: main/develop/feature/*; Jobs: build/report/release


---

# 19 roadmap okrs

# 19. Roadmap, Meilensteine & OKRs

P0 Engine/SDK/CSV; P1 Web‑IDE/Auth; P2 Sweeps/Walk‑Forward


---

# 20 risiken adrs

# 20. Risiken, Trade‑offs & ADRs

Datenqualität, Determinismus, Lizenzfragen; ADR‑Format inkl. Alternativen


---

# 21 portfolio

# 21. Portfolio‑Packaging (GitHub/LinkedIn)

README‑Pitch, Badges, Releases; LinkedIn Serie mit Metrik‑Verbesserungen


---

# 22 glossar

# 22. Glossar

Backtest, Determinismus, Drawdown, MAE/MFE, Walk‑Forward, Pareto


---

