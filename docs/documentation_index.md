# Documentation Index

## Getting Started

| Document | Description |
|----------|-------------|
| [Quickstart Guide](user_guides/quickstart_guide.md) | Create your first trading bot (Worker + Decision Logic + Config) |
| [CLI Tools Guide](cli_tools_guide.md) | All CLI commands with examples and workflow overview |
| [Worker Naming](user_guides/worker_naming_doc.md) | Worker system, naming conventions, requirements contract |

## USER Namespace

| Document | Description |
|----------|-------------|
| [USER Modules & Hot-Reload](user_guides/user_modules_and_hot_reload_mechanics.md) | Custom worker and decision logic development, auto-discovery |
| [External Workspace Setup](user_guides/external_workspace_setup.md) | Docker volume setup for external worker directories |

## Configuration

| Document | Description |
|----------|-------------|
| [Config Cascade](config_cascade_guide.md) | Two-tier config system + scenario-level parameter overrides |
| [Broker Config](broker_config_guide.md) | Multi-broker setup (MT5, Kraken), fees, symbol specifications |

## Architecture

| Document | Description |
|----------|-------------|
| [Execution Layer](architecture/architecture_execution_layer.md) | Core Sim/Live hybrid architecture, shared portfolio logic |
| [Simulation vs Live Flow](architecture/simulation_vs_live_flow.md) | Side-by-side tick flow comparison |
| [Live Execution](architecture/live_execution_architecture.md) | LiveTradeExecutor, broker polling, LiveOrderTracker |
| [Pending Order Lifecycle](architecture/pending_order_architecture.md) | 3-world model (latency, limit, stop), trigger logic |
| [Mock Adapter Guide](architecture/mock_adapter_guide.md) | MockBrokerAdapter for deterministic pipeline testing |
| [Design Decisions](architecture/execution_design_decisions.md) | Historical reasoning behind architectural choices |
| [Batch Data Flow](architecture/batch_data_flow.md) | Subprocess data channels, serialization boundaries |
| [Generator & Block Splitting](generator/generator_block_splitting_architecture.md) | Block splitting analysis, Generator Profile system, Correctness Metric |

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
| [Discovery System](discovery_system.md) | Volatility profiling, extreme moves, data coverage caching |
| [Stress Test System](stress_test.md) | Config-driven fault injection, seeded randomness |

## Test Suites

Each test suite has its own documentation in [`tests/`](tests/).

| Document | Description |
|----------|-------------|
| [Test Runner](tests/tests_runner_docs.md) | Unified runner, configuration, fail-fast |
| [Baseline Tests](tests/tests_baseline_docs.md) | Core functionality validation |
| [Margin Validation](tests/tests_margin_validation_docs.md) | Margin rejection, fill timing |
| [Multi-Position](tests/tests_multi_position_docs.md) | Concurrent position management |
| [Live Executor](tests/tests_live_executor_docs.md) | LiveTradeExecutor pipeline |
| [Pending Stats](tests/tests_pending_stats_docs.md) | Pending order statistics |
| [SL/TP & Limit Validation](tests/tests_sltp_limit_validation_docs.md) | Stop-Loss/Take-Profit, limit/stop orders |
| [Partial Close](tests/tests_partial_close_docs.md) | Partial position close |
| [Worker Tests](tests/tests_worker_docs.md) | Worker framework validation |
| [Import Pipeline](tests/tests_import_pipeline_docs.md) | Tick/bar import pipeline |
| [Data Integration](tests/tests_data_integration_docs.md) | Data chain integration |
| [Scenario Generator](generator/tests_scenario_generator_docs.md) | Block generation tests |
| [Active Order Display](tests/tests_active_order_display_docs.md) | Unresolved order reporting |
| [USER Namespace](tests/tests_user_namespace_docs.md) | USER worker/logic discovery |
| [Inter-Tick Interval](tests/inter_tick_interval_tests.md) | Market-side interval measurement |
| [Benchmark](tests/tests_benchmark_docs.md) | Performance regression (environment-specific) |
