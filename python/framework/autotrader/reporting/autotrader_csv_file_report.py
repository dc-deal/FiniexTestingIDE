"""
FiniexTestingIDE - AutoTrader CSV File Report
Writes structured trade and order logs as CSV files after session completion.
"""

import csv
from pathlib import Path
from typing import List, Optional

from python.framework.types.autotrader_types.autotrader_result_types import AutoTraderResult
from python.framework.types.portfolio_types.portfolio_trade_record_types import TradeRecord
from python.framework.types.trading_env_types.order_types import OrderResult


class AutotraderCsvFileReport:
    """
    Writes trade and order CSV logs to the session log directory.

    Creates two files per session:
    - autotrader_trades.csv — completed trade records (P&L, fees, entry/exit)
    - autotrader_orders.csv — all order results (fills, rejections)

    Args:
        run_dir: Session run directory (e.g., logs/autotrader/btcusd_mock/<timestamp>/)
    """

    def __init__(self, run_dir: Optional[Path]):
        self._run_dir = run_dir

    def write(self, result: AutoTraderResult) -> None:
        """
        Write trade and order CSV files if data is available.

        Args:
            result: Completed AutoTraderResult
        """
        if self._run_dir is None:
            return

        if result.trade_history:
            self._write_trades_csv(
                self._run_dir / 'autotrader_trades.csv',
                result.trade_history
            )

        if result.order_history:
            self._write_orders_csv(
                self._run_dir / 'autotrader_orders.csv',
                result.order_history
            )

    def _write_trades_csv(self, path: Path, trades: List[TradeRecord]) -> None:
        """
        Write trade records to CSV.

        Args:
            path: Output CSV file path
            trades: List of TradeRecord objects
        """
        fields = [
            'position_id', 'symbol', 'direction', 'lots', 'close_type',
            'entry_price', 'entry_time', 'exit_price', 'exit_time',
            'gross_pnl', 'net_pnl', 'total_fees',
            'spread_cost', 'commission_cost', 'swap_cost',
            'stop_loss', 'take_profit', 'close_reason', 'entry_type',
            'contract_size', 'digits', 'account_currency', 'comment',
        ]
        try:
            with open(path, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(fields)
                for t in trades:
                    writer.writerow([
                        t.position_id,
                        t.symbol,
                        t.direction.value if hasattr(t.direction, 'value') else t.direction,
                        t.lots,
                        t.close_type.value if hasattr(t.close_type, 'value') else t.close_type,
                        t.entry_price,
                        t.entry_time.isoformat() if t.entry_time else '',
                        t.exit_price,
                        t.exit_time.isoformat() if t.exit_time else '',
                        t.gross_pnl,
                        t.net_pnl,
                        t.total_fees,
                        t.spread_cost,
                        t.commission_cost,
                        t.swap_cost,
                        t.stop_loss if t.stop_loss is not None else '',
                        t.take_profit if t.take_profit is not None else '',
                        t.close_reason.value if hasattr(t.close_reason, 'value') else t.close_reason,
                        t.entry_type.value if hasattr(t.entry_type, 'value') else t.entry_type,
                        t.contract_size,
                        t.digits,
                        t.account_currency,
                        t.comment,
                    ])
        except Exception as e:
            print(f"Warning: Failed to write trade CSV: {e}")

    def _write_orders_csv(self, path: Path, orders: List[OrderResult]) -> None:
        """
        Write order results to CSV.

        Args:
            path: Output CSV file path
            orders: List of OrderResult objects
        """
        fields = [
            'order_id', 'status', 'executed_price', 'executed_lots',
            'execution_time', 'commission', 'swap', 'slippage_points',
            'rejection_reason', 'rejection_message', 'broker_order_id',
        ]
        try:
            with open(path, 'w', newline='') as f:
                writer = csv.writer(f)
                writer.writerow(fields)
                for o in orders:
                    writer.writerow([
                        o.order_id,
                        o.status.value if hasattr(o.status, 'value') else o.status,
                        o.executed_price if o.executed_price is not None else '',
                        o.executed_lots if o.executed_lots is not None else '',
                        o.execution_time.isoformat() if o.execution_time else '',
                        o.commission,
                        o.swap,
                        o.slippage_points,
                        o.rejection_reason.value if o.rejection_reason and hasattr(o.rejection_reason, 'value') else (o.rejection_reason or ''),
                        o.rejection_message,
                        o.broker_order_id or '',
                    ])
        except Exception as e:
            print(f"Warning: Failed to write order CSV: {e}")
