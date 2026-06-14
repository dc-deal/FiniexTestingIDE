# Documentation Index

## Getting Started

| Document | Description |
|----------|-------------|
| [Quickstart Guide](user_guides/quickstart_guide.md) | Create your first trading bot (Worker + Decision Logic + Config) |
| [CLI Tools Guide](cli_tools_guide.md) | All CLI commands with examples and workflow overview |
| [Worker Naming](user_guides/worker_naming_doc.md) | Worker reference system, path-based loading, requirements contract |
| [Algo State Persistence](user_guides/algo_state_persistence_guide.md) | Restart-safe algo memory — snapshot/restore hooks, staleness, JSON contract |

## User Algo Workspace

| Document | Description |
|----------|-------------|
| [User Algo Workspace & Loading](user_guides/user_modules_and_hot_reload_mechanics.md) | user_algos/ layout, on-demand loading, hot-reload mechanics |
| [External Workspace Setup](user_guides/external_workspace_setup.md) | Docker volume setup for external algo directories |
| [FiniexViewer Setup](user_guides/finiexviewer_setup.md) | Visual frontend — dual-repo dev environment, API server, Vite dev server |

## Configuration

| Document | Description |
|----------|-------------|
| [Config Cascade](config_cascade_guide.md) | Multi-level config cascade (app → global → scenario), parameter overrides |
| [user_configs/ Override System](user_configs_override_system.md) | How `user_configs/` overrides `configs/` — content-merge vs file-replace, list-merge by identifier |
| [Broker Config](broker_config_guide.md) | Multi-broker setup (MT5, Kraken), fees, symbol specifications |

## AutoTrader (Live Trading)

| Document | Description |
|----------|-------------|
| [AutoTrader Architecture](autotrader/autotrader_architecture.md) | Pipeline, threading model, config, tick sources, clipping monitor |
| [Adapter Development Guide](user_guides/adapter/adapter_development_guide.md) | How to implement a new broker adapter (Tier 1/2/3, config files, credentials, test suite) |
| [Kraken Adapter Setup](user_guides/adapter/setup_kraken_adapter.md) | API keys, broker settings, dry-run, first live run |

## Architecture

| Document | Description |
|----------|-------------|
| [Execution Layer](architecture/architecture_execution_layer.md) | Core Sim/Live hybrid architecture, shared portfolio logic |
| [Simulation vs Live Flow](architecture/simulation_vs_live_flow.md) | Side-by-side tick flow comparison |
| [Live Execution](architecture/live_execution_architecture.md) | LiveTradeExecutor, broker polling, LiveOrderTracker |
| [Pending Order Lifecycle](architecture/pending_order_architecture.md) | 3-world model (latency, limit, stop), trigger logic |
| [Broker Trade Records](architecture/broker_trade_records.md) | Order ↔ executions pairing model, BrokerTrade type, Tier-3 trades-query layer |
| [Trade Execution Visibility](architecture/trade_execution_visibility.md) | Trigger / BrokerOrder / Fills three-level model, Position.entry_trades + TradeRecord.entry_trades / exit_trades propagation, sub-line rendering, long-format event-stream CSV (#330) |
| [Drift Audit](architecture/drift_audit.md) | Read-only local-vs-broker drift telemetry (#327) — FEE / VOLUME / PRICE counters, async trades-query consumer, live-display footer |
| [Decision Event Channel](architecture/decision_event_channel.md) | Typed ordered event channel — order/fill/cancel/partial-close/session-end hooks for decision logic, drain-at-boundary, request_session_end (#348) |
| [Mock Adapter Guide](architecture/mock_adapter_guide.md) | MockBrokerAdapter for deterministic pipeline testing |
| [Order Guard](architecture/order_guard_architecture.md) | Pre-validation guard (SHORT+SPOT, rejection cooldown, async callback) |
| [Performance Tracking Layers](architecture/performance_tracking_layers.md) | Two-layer model (per-component + tick-loop profiler), defaults, graceful degradation, why no context-manager wrappers in the tick loop |
| [Safety Circuit Breaker](architecture/safety_circuit_breaker_architecture.md) | Account-level protection (balance/drawdown thresholds, AutoTrader only) |
| [Design Decisions](architecture/execution_design_decisions.md) | Historical reasoning behind architectural choices |
| [Batch Data Flow](architecture/batch_data_flow.md) | Subprocess data channels, serialization boundaries |
| [Market Capabilities](architecture/market_capabilities.md) | Worker activity metric declaration, pre-flight compatibility validation |
| [Normalization System](architecture/normalization_system.md) | `Normalizer` — central rescale/clamp/normalize for cross-instrument-comparable indicator values |
| [Diagnostics CSV Sink](architecture/diagnostics_csv_sink.md) | Generic per-run CSV channel — framework owns file logistics, strategy owns the schema (both pipelines) |
| [Generator & Block Splitting](generator/generator_block_splitting_architecture.md) | Block splitting analysis, Generator Profile system, Correctness Metric |
| [API Server Architecture](architecture/api_server_architecture.md) | FastAPI foundation, CORS, endpoint guide, cache integration note |

## Data Pipeline

| Document | Description |
|----------|-------------|
| [TickCollector Guide](data_pipeline/tick_collector_guide.md) | MQL5 data collection, JSON schema, error classification |
| [Data Import Pipeline](data_pipeline/data_import_pipeline.md) | JSON→Parquet conversion, UTC normalization, bar rendering |
| [Batch Preparation](data_pipeline/batch_preparation_system.md) | 7-phase orchestration system |
| [Duplicate Detection](data_pipeline/duplicate_detection_usage.md) | Artificial duplicate detection, data integrity protection |

## System

| Document | Description |
|----------|-------------|
| [Process Execution](process_execution_guide.md) | Subprocess architecture, ProcessPoolExecutor |
| [Tick Processing Budget](tick_processing_budget_guide.md) | Deterministic clipping simulation, virtual clock filtering |
| [Discovery System](discovery_system.md) | Volatility profiling, extreme moves, data coverage caching |
| [Stress Test System](stress_test.md) | Config-driven fault injection, seeded randomness |

## Test Suites

Each test suite has its own documentation in [`tests/`](tests/).

| Document | Description |
|----------|-------------|
| [Test Runner](tests/tests_runner_docs.md) | Unified runner, configuration, fail-fast |
| [Bar Parity Tests](tests/parity/bar_parity_tests.md) | Cross-pipeline parity: simulation vs. AutoTrader bar identity |
| [Heartbeat Ghost-Pass Parity](tests/parity/heartbeat_ghost_tests.md) | Sim ghost-pass between ticks + weekend-gap gate (#360 Stage 2) |
| [AutoTrader Integration](tests/autotrader/integration_tests.md) | End-to-end mock session validation |
| [Kraken Adapter Live Integration](tests/live_adapters/kraken_adapter_integration_tests.md) | Dry-run order lifecycle against real Kraken API — real account required, release-gate |
| [Live Field Study](tests/live_field_study/field_study_guide.md) | End-to-end live acceptance test + PASS/FAIL certificate — operator-driven, release-gate (#332) |
| [Safety Circuit Breaker](tests/autotrader/safety_tests.md) | Equity-based safety, phantom drawdown fix, config split |
| [Live Executor](tests/autotrader/live_executor_tests.md) | LiveTradeExecutor pipeline |
| [Loop Cadence](tests/autotrader/loop_cadence_tests.md) | Clock injection, heartbeat re-poll, decision ghost-pass (#360) |
| [Algo State Persistence](tests/autotrader/state_persistence_tests.md) | Snapshot store, corrupt/stale policy, weekend-aware staleness, pre-flight (#354) |
| [Order Guard](tests/autotrader/order_guard_tests.md) | Rejection cooldown, async callback |
| [Reconciliation](tests/autotrader/reconciliation_tests.md) | Broker truth-pull + Reconciler ALERT_ONLY (#151) |
| [API Monitor](tests/autotrader/api_monitor_tests.md) | Per-endpoint broker REST latency/error telemetry (#351) |
| [Kraken Adapter Nonce](tests/autotrader/kraken_adapter_tests.md) | Private-call nonce monotonicity + lock (#332) |
| [Baseline Tests](tests/simulation/baseline_tests.md) | Core functionality validation |
| [Margin Validation](tests/simulation/margin_validation_tests.md) | Margin rejection, fill timing |
| [Multi-Position](tests/simulation/multi_position_tests.md) | Concurrent position management |
| [Pending Stats](tests/simulation/pending_stats_tests.md) | Pending order statistics |
| [SL/TP & Limit Validation](tests/simulation/sltp_limit_validation_tests.md) | Stop-Loss/Take-Profit, limit/stop orders |
| [Partial Close](tests/simulation/partial_close_tests.md) | Partial position close |
| [Active Order Display](tests/simulation/active_order_display_tests.md) | Unresolved order reporting |
| [Spot SELL](tests/simulation/spot_sell_tests.md) | Spot BUY/SELL execution, insufficient base balance rejection |
| [Tick Clipping](tests/simulation/tick_clipping_tests.md) | Bar rendering correctness under tick processing budget clipping |
| [Event Channel](tests/simulation/event_channel_tests.md) | Decision event channel dual-world parity (#348) |
| [Order Precision](tests/simulation/order_precision_tests.md) | Order price → digits normalization (#332) |
| [Benchmark](tests/simulation/benchmark_tests.md) | Performance regression (environment-specific) |
| [Import Pipeline](tests/data/import_pipeline_tests.md) | Tick/bar import pipeline |
| [Data Integration](tests/data/data_integration_tests.md) | Data chain integration |
| [Inter-Tick Interval](tests/data/inter_tick_interval_tests.md) | Market-side interval measurement |
| [Tick Processing Budget](tests/data/tick_processing_budget_tests.md) | Virtual clock filtering, ClippingStats |
| [Scenario Generator](generator/tests_scenario_generator_docs.md) | Block generation tests |
| [Batch Validations](tests/framework/batch_validations_tests.md) | Phase 0 validation: ScenarioValidator, BrokerDataPreparator map filtering |
| [Config Tests (Cascade + Merge Utility)](tests/framework/config_cascade_tests.md) | execution_config 3-level cascade, nested sub-group merge, unknown-key safety net (#137), deep_merge list_merge_keys unit tests |
| [Worker Tests](tests/framework/worker_tests.md) | Worker framework validation |
| [Normalizer Tests](tests/framework/normalizer_tests.md) | Central rescale/clamp/normalize apparatus |
| [Diagnostics CSV Sink Tests](tests/framework/diagnostics_csv_sink_tests.md) | Strategy-owned diagnostics CSV channel + flush helper |
| [Bar Rendering Consistency](tests/framework/bar_rendering_tests.md) | BarRenderer vs VectorizedBarRenderer equivalence |
| [Tick Parquet Reader](tests/framework/tick_parquet_reader_tests.md) | Column normalization, volume chain integration |
| [API Endpoint Tests](tests/framework/api_endpoint_tests.md) | Health, brokers, symbols, coverage, bars — mocked, no parquet required |
| [Path-Based Loading](tests/framework/user_namespace_tests.md) | Worker/logic path loading, introspection, CORE integrity |
| [Market Compatibility](tests/framework/market_compatibility_tests.md) | Worker activity metric declaration, pre-flight scenario rejection |
| [Algo Clock Convention](tests/framework/algo_clock_tests.md) | §9 wall-clock ban lint (decision logic/workers, CI plane) |
| [Algo Clock Validator](tests/framework/algo_clock_validator_tests.md) | §9 runtime startup validator — AST scan of loaded algos (CORE + USER) + batch pre-flight |
