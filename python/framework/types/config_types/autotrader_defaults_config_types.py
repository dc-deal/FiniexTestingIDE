"""
FiniexTestingIDE - AutoTrader Defaults Configuration Types
Pydantic models for the app_config.json::autotrader section.

Note: This covers only the app_config defaults section — not the full
AutoTraderConfig profile type defined in autotrader_config_types.py.
"""
from typing import Literal

from pydantic import BaseModel

from python.framework.types.config_types.performance_tracking_config_types import (
    AutoTraderPerformanceTrackingConfig,
)


class AutotraderExecutionDefaults(BaseModel):
    """AutoTrader tick-loop execution defaults."""
    parallel_workers: bool = False
    bar_max_history: int = 1000
    # Idle-heartbeat cadence (#360): max wait for a real tick before the loop
    # fires a timer event (drain + reconcile + re-poll + decision ghost-pass).
    # Governs the live idle wake only; does not multiply broker I/O (re-poll is
    # gated by poll_interval_ms, reconcile by min_interval_seconds). 500 ms for
    # snappier between-tick reaction; event-driven wake-on-arrival is #331.
    heartbeat_interval_ms: int = 500
    # Market-data staleness contract (#436): no real tick for this many wall
    # seconds → session-level stale (pot warning + on_market_data_stale hook +
    # OrderGuard entry block). Evaluated on the idle heartbeat, LIVE loop only
    # (sim replay gaps are data). 0 disables the contract. Per-profile
    # overridable; matches the sim inter_tick_gap_threshold_s magnitude.
    market_data_stale_after_s: float = 300.0
    performance_tracking: AutoTraderPerformanceTrackingConfig = AutoTraderPerformanceTrackingConfig()


class ClippingMonitorDefaults(BaseModel):
    """Clipping monitor defaults."""
    report_interval_s: float = 60.0
    strategy: str = 'queue_all'
    # Post-session advisory: warn when the clipping ratio exceeds this share of ticks. Unlike
    # an absolute millisecond threshold, this one is grounded — the ratio is already measured
    # against real tick arrival, so it says how often the algo failed to keep up. Where the
    # line sits is a policy question, which is why it is config and not a constant. A ratio
    # can never exceed 1.0, so 1.0 disables the advisory.
    warn_above_ratio: float = 0.05


class DisplayDefaults(BaseModel):
    """Live console dashboard defaults."""
    enabled: bool = True
    update_interval_ms: int = 300


class OrderGuardDefaults(BaseModel):
    """Order guard pre-validation defaults."""
    cooldown_seconds: float = 60.0
    max_consecutive_rejections: int = 2
    # #436 framework floor: reject NEW entries while market data is stale
    # (closes/cancels unaffected). Inert in sim — status is always fresh there.
    block_stale_market_data: bool = True


class DriftAuditConfig(BaseModel):
    """
    Read-only drift telemetry defaults (#327).

    Compares locally-computed fee/volume/price against broker-reported truth
    via the #326 trades-query pipeline. Logs drift events above thresholds.
    Does not mutate state — purely observational. Correction is #151.
    """
    enabled: bool = True
    fee_threshold_pct: float = 0.5       # Bug-signal threshold for fee drift
    volume_threshold_pct: float = 0.1    # Partial-fill signal
    price_threshold_pct: float = 1.0     # Kraken-intra-reporting consistency (QueryOrder vs QueryTrades)
    slippage_threshold_pct: float = 0.5  # Submission tick mid vs broker fill price (#340)
    log_all: bool = False                # If True, log every event (not just threshold breaches)
    sample_rate: float = 1.0             # Reserved notausgang; V1.3 default = audit every fill


class ReconciliationDefaults(BaseModel):
    """
    Live reconciliation defaults (#151) — broker truth-pull cadence + mode.

    Detects divergence between local shadow state and broker truth. ALERT_ONLY
    only in V1.3 (detect + log + SESSION counter, no mutation); AUTO_CORRECT /
    HALT_TRADING land in #349. Live-only — mock adapters auto-disable in the loader.
    """
    enabled: bool = True
    mode: Literal['alert_only', 'auto_correct', 'halt_trading'] = 'alert_only'
    interval_ticks: int = 100          # reconcile every N ticks ...
    min_interval_seconds: float = 60.0  # ... OR every M wall-clock seconds (hybrid)


class ApiMonitorConfig(BaseModel):
    """
    Broker REST transport-latency monitor defaults (#351).

    Per-endpoint latency + error/reject telemetry, own live panel, plus logging
    of the abnormal (failed calls + calls slower than slow_call_threshold_ms).
    Live-only; default ON for live (mock auto-disabled in the loader).
    """
    enabled: bool = True
    slow_call_threshold_ms: float = 3000.0  # calls slower than this are logged + flagged


class StatePersistenceDefaults(BaseModel):
    """
    Algo state persistence defaults (#354) — restart-safe algo memory (Category B).

    Live-only; mock auto-disabled in the loader. Opt-in per algo via
    AbstractDecisionLogic.uses_state_persistence() — the whole subsystem (store,
    restore, stale-check, boot pre-flight) is skipped for algos that do not declare it.
    Staleness is weekend-aware (trading days via MarketCalendar) so a Friday-night
    snapshot is not counted as 3 days old on Monday.
    """
    enabled: bool = True
    path: str = 'data/runtime/session_state'
    save_interval_ticks: int = 500           # save every N ticks ...
    save_interval_seconds: float = 60.0      # ... OR every M wall-clock seconds (hybrid)
    max_age_trading_days: int = 5            # discard restored state older than this (0 = no guard)
    on_corrupt: Literal['warn_reset', 'fail'] = 'warn_reset'   # corrupt file: reset fresh or refuse to start
    on_stale: Literal['warn_reset', 'halt'] = 'warn_reset'     # too-old state: reset fresh or halt boot


class ColdStartDefaults(BaseModel):
    """
    Cold-start recovery defaults (#355) — adopting our own resting orders on boot.

    Live-only. On boot the executor's shadow state is empty while the venue still holds what
    an earlier session left there; adoption rebuilds the ORDERS whose ownership is decidable
    (they carry our client order id). Balances are NOT adopted — a coin carries no owner tag,
    so what a bot may use is DECLARED capital, not a guess (see the capital-allocation issue).

    adoption_mode 'operator_confirm' asks only where a terminal exists and otherwise refuses
    loudly and stays flat: it never waits for an answer nobody is there to give. An unattended
    run is a conscious 'auto'.

    book_drift_interval_ticks bounds the second, cheaper half of the position-book write.
    A structural change (a position opens, closes, or is partially closed) is written at once
    — it cannot be recovered. Exit levels and excursion extrema move far more often (a
    trailing stop moves on every new high) and are either re-derived by the algo or lose at
    most one interval of history, so they wait for this window. Measured on this project's
    tree: one write costs 11 ms (§42), which is why the frequent half is not immediate.

    Counted in TICKS rather than seconds, and that is not a detail: drift is CAUSED by ticks
    (a price that does not move sets no new extreme and moves no trailing stop), so a quiet
    market needs no writes at all. It also needs no clock, which matters because the boot and
    the first heartbeat happen before the canonical clock is injected.
    """
    enabled: bool = True
    path: str = 'data/runtime/cold_start_state'
    adoption_mode: Literal['operator_confirm', 'auto'] = 'operator_confirm'
    book_drift_interval_ticks: int = 500


class SessionEndDefaults(BaseModel):
    """
    What a live session does with what it still holds when it ends (#492).

    TWO decisions of very different weight, which one setting used to answer together:
    cancelling a resting order costs nothing but a missed fill, closing a position
    realises P&L and pays spread and fee.

    `orders`
        'cancel' cancels every resting order AT THE VENUE. 'leave' lets them stand, which
        is what a bot meant to survive a restart usually wants (#355 adopts them back on
        the next boot). It is also the LOOSENING value — afterwards orders sit at a venue
        with nobody watching — so a profile may only choose it when the broker's own
        posture allows it (market_config.json::session_end_orders), the same asymmetry
        `dry_run` carries.

    `positions`
        'leave' lets an open position stand and reports it as OPEN and valued. That is the
        default and it is what every professional system does: a position belongs to the
        ACCOUNT, not to the process, and no venue offers a position counterpart to the
        order-side Cancel-on-Disconnect. 'close' is DECLARED but NOT BUILT — a close that
        really reaches the venue is an asynchronous live order whose fill arrives on the
        next tick, and at session end the tick source is already stopped; it needs #487's
        resolution discipline first, so it refuses at startup instead of pretending.

    What used to happen was neither: the position was closed in OUR BOOK only, which
    reported a realised exit that never reached the venue.

    The EMERGENCY is not a third value here. One code path answering both "the session is
    over" and "something went wrong" is the confusion this setting exists to end;
    emergency flattening belongs to the safety baseline and stays there.
    """
    orders: Literal['cancel', 'leave'] = 'cancel'
    positions: Literal['close', 'leave'] = 'leave'


class AutotraderDefaultsConfig(BaseModel):
    """
    Top-level model for app_config.json::autotrader.
    Provides global defaults merged into every AutoTrader profile at load time.
    """
    execution: AutotraderExecutionDefaults = AutotraderExecutionDefaults()
    clipping_monitor: ClippingMonitorDefaults = ClippingMonitorDefaults()
    display: DisplayDefaults = DisplayDefaults()
    order_guard: OrderGuardDefaults = OrderGuardDefaults()
    drift_audit: DriftAuditConfig = DriftAuditConfig()
    reconciliation: ReconciliationDefaults = ReconciliationDefaults()
    api_monitor: ApiMonitorConfig = ApiMonitorConfig()
    state_persistence: StatePersistenceDefaults = StatePersistenceDefaults()
    cold_start: ColdStartDefaults = ColdStartDefaults()
    session_end: SessionEndDefaults = SessionEndDefaults()
