"""
FiniexTestingIDE - Active Stop Order Reporting Tests
Validates active_stop_orders in pending_stats for scenario_02_active_stop_display.

Tests:
- active_stop_orders has exactly 1 entry at scenario end
- Entry direction, order_type, and entry_price match config
- active_limit_orders is empty (no limit orders in this scenario)
"""

from tests.shared.shared_active_order_display import TestActiveStopOrdersReported

# All test classes imported from shared module.
# Pytest discovers them via this import.
