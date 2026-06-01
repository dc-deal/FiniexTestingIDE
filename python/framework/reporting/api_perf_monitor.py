"""
FiniexTestingIDE - API Performance Monitor (#351)

Read-only broker REST transport telemetry: times every private API call per
endpoint and records latency + failures. Thread-safe — record() is called from
the tick-loop thread (sync reconcile / warmup) AND from worker threads
(#319/#320/#327 async dispatch). Logs only the abnormal: failed calls and calls
slower than the configured threshold. Surfaced live via the API PERFORMANCE
panel and a final per-endpoint summary.

Experimental first cut — aggregate per-endpoint metrics + threshold logging.
A rigorous view wants the full per-endpoint return-speed distribution over time.
"""

import threading
from dataclasses import replace
from datetime import datetime, timezone
from typing import Dict, Optional

from python.framework.logging.abstract_logger import AbstractLogger
from python.framework.types.config_types.autotrader_defaults_config_types import ApiMonitorConfig
from python.framework.types.live_types.api_perf_types import ApiEndpointStats, ApiPerfSnapshot


class ApiPerfMonitor:
    """
    Per-endpoint broker REST latency/error monitor (live-only).

    One `ApiEndpointStats` per distinct endpoint, updated in place; the live
    panel renders one row per endpoint (a new endpoint adds a row, repeat calls
    update it). Failures and slow calls are logged with an `[API]` prefix.

    Args:
        config: ApiMonitorConfig — slow-call threshold.
        logger: AbstractLogger — session logger.
    """

    def __init__(self, config: ApiMonitorConfig, logger: AbstractLogger):
        self._config = config
        self._logger = logger
        self._lock = threading.Lock()
        self._endpoints: Dict[str, ApiEndpointStats] = {}
        self._slow_count: int = 0
        self._total_errors: int = 0

        self._logger.info(
            f"📡 API Performance Monitor active — slow threshold "
            f"{config.slow_call_threshold_ms:.0f}ms"
        )

    def record(
        self,
        endpoint: str,
        duration_ms: float,
        success: bool = True,
        error: Optional[str] = None,
    ) -> None:
        """
        Record one broker REST call. Thread-safe.

        Args:
            endpoint: Endpoint identifier (e.g. '/0/private/OpenOrders')
            duration_ms: Call duration in milliseconds
            success: False for a Kraken `error` response / transport failure
            error: Failure message (when success is False)
        """
        with self._lock:
            stats = self._endpoints.get(endpoint)
            if stats is None:
                stats = ApiEndpointStats(endpoint=endpoint)
                self._endpoints[endpoint] = stats
            stats.count += 1
            stats.last_fired_at = datetime.now(timezone.utc)
            stats.last_ms = duration_ms
            stats.total_ms += duration_ms
            if stats.count == 1:
                stats.min_ms = duration_ms
                stats.max_ms = duration_ms
            else:
                stats.min_ms = min(stats.min_ms, duration_ms)
                stats.max_ms = max(stats.max_ms, duration_ms)
            if not success:
                stats.error_count += 1
                stats.last_error = error
                self._total_errors += 1
            is_slow = duration_ms > self._config.slow_call_threshold_ms
            if is_slow:
                self._slow_count += 1

        # Log outside the lock — only the abnormal.
        if not success:
            self._logger.warning(
                f"[API] {endpoint} failed after {duration_ms:.0f}ms: {error}"
            )
        elif is_slow:
            self._logger.warning(
                f"[API] {endpoint} slow: {duration_ms:.0f}ms "
                f"(threshold {self._config.slow_call_threshold_ms:.0f}ms)"
            )

    def get_snapshot(self) -> ApiPerfSnapshot:
        """
        Return a copy of the current per-endpoint state, busiest endpoint first.

        Returns:
            ApiPerfSnapshot — safe to read from the display thread.
        """
        with self._lock:
            endpoints = [replace(s) for s in self._endpoints.values()]
            slow = self._slow_count
            errors = self._total_errors
        endpoints.sort(key=lambda s: s.count, reverse=True)
        return ApiPerfSnapshot(endpoints=endpoints, slow_count=slow, total_errors=errors)

    def shutdown(self) -> None:
        """Emit a final per-endpoint summary to the session log."""
        snapshot = self.get_snapshot()
        if not snapshot.endpoints:
            self._logger.info("📡 API Performance final: no calls recorded")
            return
        lines = [
            f"   {s.endpoint}: {s.count} calls | avg {s.avg_ms:.0f}ms | "
            f"min {s.min_ms:.0f}ms | max {s.max_ms:.0f}ms | errors {s.error_count}"
            for s in snapshot.endpoints
        ]
        self._logger.info(
            "📡 API Performance final:\n" + "\n".join(lines)
            + f"\n   slow (>{self._config.slow_call_threshold_ms:.0f}ms): "
            f"{snapshot.slow_count} | total errors: {snapshot.total_errors}"
        )
