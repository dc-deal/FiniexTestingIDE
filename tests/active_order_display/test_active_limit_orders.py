"""
FiniexTestingIDE - Active Limit Order Reporting Tests
Validates active_limit_orders in pending_stats for scenario_01_active_limit_display.

Tests:
- active_limit_orders has exactly 1 entry at scenario end
- Entry direction, order_type, and entry_price match config
- active_stop_orders is empty (no stop orders in this scenario)
"""

from tests.shared.shared_active_order_display import TestActiveLimitOrdersReported

# All test classes imported from shared module.
# Pytest discovers them via this import.
