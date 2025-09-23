"""
FiniexTestingIDE Test Engine
Orchestrates complete strategy testing workflow
"""

import pandas as pd
import numpy as np
from pathlib import Path
from datetime import datetime, timezone
from typing import Dict, List, Any, Optional, Tuple
import json
import logging

from python.data_loader import TickDataLoader
from python.blackbox_framework import BlackboxBase, Tick, Signal, TestContext
from python.performance_calculator import PerformanceCalculator

logger = logging.getLogger(__name__)

class TradeExecutor:
    """Converts strategy signals to executed trades"""
    
    def __init__(self, initial_balance: float = 100000.0, 
                 default_position_size: float = 0.02,
                 spread_points: float = 1.5,
                 slippage_points: float = 0.5):
        self.initial_balance = initial_balance
        self.current_balance = initial_balance
        self.default_position_size = default_position_size
        self.spread_points = spread_points
        self.slippage_points = slippage_points
        
        # Current state
        self.current_position = 0.0  # Position size (positive=long, negative=short)
        self.position_entry_price = 0.0
        self.trades = []
        self.equity_curve = [initial_balance]
        
    def execute_signal(self, signal: Signal, tick: Tick) -> Optional[Dict]:
        """Execute trading signal and return trade info if position changed"""
        
        if signal.action == "FLAT":
            return None
            
        # Calculate execution price with spread and slippage
        if signal.action == "BUY":
            execution_price = tick.ask + (self.slippage_points * tick.symbol_info.get('point_value', 0.00001))
            desired_position = abs(signal.quantity) if signal.quantity != 0 else self.default_position_size
        else:  # SELL
            execution_price = tick.bid - (self.slippage_points * tick.symbol_info.get('point_value', 0.00001))
            desired_position = -abs(signal.quantity) if signal.quantity != 0 else -self.default_position_size
            
        # Check if position change needed
        if abs(self.current_position - desired_position) < 0.001:
            return None  # No significant position change
            
        # Close existing position if opposite direction
        trade_info = None
        if (self.current_position > 0 and desired_position <= 0) or \
           (self.current_position < 0 and desired_position >= 0):
            trade_info = self._close_position(tick, execution_price, signal.comment)
            
        # Open new position
        if desired_position != 0:
            self._open_position(desired_position, execution_price, tick, signal)
            
        return trade_info
        
    def _close_position(self, tick: Tick, exit_price: float, comment: str) -> Dict:
        """Close current position and calculate P&L"""
        if self.current_position == 0:
            return None
            
        # Calculate P&L
        if self.current_position > 0:  # Closing long position
            pnl = (exit_price - self.position_entry_price) * abs(self.current_position)
        else:  # Closing short position  
            pnl = (self.position_entry_price - exit_price) * abs(self.current_position)
            
        # Create trade record
        trade = {
            'entry_time': getattr(self, 'position_entry_time', tick.timestamp),
            'exit_time': tick.timestamp,
            'direction': 'LONG' if self.current_position > 0 else 'SHORT',
            'quantity': abs(self.current_position),
            'entry_price': self.position_entry_price,
            'exit_price': exit_price,
            'pnl': pnl,
            'comment': comment
        }
        
        # Update balance and state
        self.current_balance += pnl
        self.trades.append(trade)
        self.current_position = 0.0
        self.position_entry_price = 0.0
        
        logger.debug(f"Closed position: {trade['direction']} P&L: {pnl:.2f}")
        return trade
        
    def _open_position(self, position_size: float, entry_price: float, tick: Tick, signal: Signal):
        """Open new position"""
        self.current_position = position_size
        self.position_entry_price = entry_price
        self.position_entry_time = tick.timestamp
        
        direction = 'LONG' if position_size > 0 else 'SHORT'
        logger.debug(f"Opened {direction} position: {abs(position_size)} @ {entry_price:.5f}")
        
    def update_equity(self, current_tick: Tick):
        """Update equity curve with current unrealized P&L"""
        unrealized_pnl = 0.0
        
        if self.current_position != 0:
            current_price = current_tick.mid_price
            if self.current_position > 0:  # Long position
                unrealized_pnl = (current_price - self.position_entry_price) * self.current_position
            else:  # Short position
                unrealized_pnl = (self.position_entry_price - current_price) * abs(self.current_position)
                
        current_equity = self.current_balance + unrealized_pnl
        self.equity_curve.append(current_equity)

class TestEngine:
    """Main testing engine orchestrating complete strategy test"""
    
    def __init__(self, data_loader: TickDataLoader):
        self.data_loader = data_loader
        self.results = {}
        
    def run_test(self, strategy_class, strategy_params: Dict, test_config: Dict) -> Dict[str, Any]:
        """
        Run complete strategy test
        
        Args:
            strategy_class: BlackboxBase subclass
            strategy_params: Parameters for strategy
            test_config: Test configuration (symbol, data_mode, etc.)
            
        Returns:
            Complete test results dictionary
        """
        
        logger.info(f"Starting test: {strategy_class.__name__} on {test_config['symbol']}")
        
        # Load data
        symbol = test_config['symbol']
        data_mode = test_config.get('data_mode', 'realistic')
        
        tick_data = self.data_loader.load_symbol_data(
            symbol=symbol,
            start_date=test_config.get('start_date'),
            end_date=test_config.get('end_date')
        )
        
        if len(tick_data) == 0:
            raise ValueError(f"No tick data available for {symbol}")
            
        logger.info(f"Loaded {len(tick_data):,} ticks for {symbol}")
        
        # Initialize strategy
        strategy = strategy_class(debug_enabled=test_config.get('debug_enabled', False))
        strategy.set_parameters(strategy_params)
        
        # Initialize trade executor
        executor = TradeExecutor(
            initial_balance=test_config.get('initial_balance', 100000.0),
            default_position_size=test_config.get('position_size', 0.02),
            spread_points=test_config.get('spread_points', 1.5),
            slippage_points=test_config.get('slippage_points', 0.5)
        )
        
        # Create test context
        context = TestContext(
            symbol=symbol,
            start_time=tick_data['timestamp'].iloc[0],
            end_time=tick_data['timestamp'].iloc[-1],
            data_mode=data_mode
        )
        
        # Run strategy lifecycle
        strategy.on_start(context)
        
        # Main processing loop
        processed_ticks = 0
        signals_generated = 0
        trades_executed = 0
        
        for _, row in tick_data.iterrows():
            # Convert DataFrame row to Tick object
            tick = Tick(
                symbol=symbol,
                timestamp=row['timestamp'].isoformat(),
                bid=float(row['bid']),
                ask=float(row['ask']),
                volume=float(row.get('tick_volume', 0)),
                spread_points=float(row.get('spread_points', 0))
            )
            
            # Get strategy signal
            signal = strategy.on_tick(tick)
            if signal and signal.action != "FLAT":
                signals_generated += 1
                
                # Execute signal
                trade = executor.execute_signal(signal, tick)
                if trade:
                    trades_executed += 1
                    
            # Update equity curve
            executor.update_equity(tick)
            processed_ticks += 1
            
            # Progress logging
            if processed_ticks % 10000 == 0:
                logger.info(f"Processed {processed_ticks:,} ticks, "
                          f"Signals: {signals_generated}, Trades: {trades_executed}")
        
        # Finalize test
        strategy.on_stop(context)
        
        # Close any remaining position
        if executor.current_position != 0:
            final_tick = tick  # Last processed tick
            final_trade = executor._close_position(
                final_tick, final_tick.mid_price, "End of test"
            )
            if final_trade:
                trades_executed += 1
        
        # Calculate performance metrics
        metrics = PerformanceCalculator.calculate_metrics(
            executor.trades, executor.equity_curve
        )
        
        # Compile results
        results = {
            'strategy_info': {
                'name': strategy_class.__name__,
                'parameters': strategy_params,
                'parameter_schema': strategy.get_parameter_schema()
            },
            'test_config': test_config,
            'execution_summary': {
                'processed_ticks': processed_ticks,
                'signals_generated': signals_generated,
                'trades_executed': trades_executed,
                'start_time': context.start_time,
                'end_time': context.end_time,
                'test_duration_minutes': (processed_ticks / 60) if processed_ticks > 0 else 0  # Rough estimate
            },
            'performance_metrics': metrics,
            'trades': executor.trades,
            'equity_curve': executor.equity_curve,
            'visual_elements': strategy.visual_elements if hasattr(strategy, 'visual_elements') else [],
            'timestamp': datetime.now(timezone.utc).isoformat()
        }
        
        logger.info(f"Test completed: {trades_executed} trades, "
                   f"Final P&L: {executor.current_balance - executor.initial_balance:.2f}")
        
        return results
        
    def export_results(self, results: Dict, output_dir: Path, formats: List[str] = None):
        """Export test results in specified formats"""
        if formats is None:
            formats = ['json', 'csv']
            
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        
        strategy_name = results['strategy_info']['name']
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        base_filename = f"{strategy_name}_{results['test_config']['symbol']}_{timestamp}"
        
        if 'json' in formats:
            # Export complete results as JSON
            json_file = output_dir / f"{base_filename}_results.json"
            with open(json_file, 'w') as f:
                json.dump(results, f, indent=2, default=str)
            logger.info(f"Exported JSON results: {json_file}")
            
        if 'csv' in formats:
            # Export trades as CSV
            if results['trades']:
                trades_df = pd.DataFrame(results['trades'])
                csv_file = output_dir / f"{base_filename}_trades.csv"
                trades_df.to_csv(csv_file, index=False)
                logger.info(f"Exported trades CSV: {csv_file}")
                
            # Export equity curve as CSV
            equity_df = pd.DataFrame({
                'tick_number': range(len(results['equity_curve'])),
                'equity': results['equity_curve']
            })
            equity_file = output_dir / f"{base_filename}_equity.csv"
            equity_df.to_csv(equity_file, index=False)
            logger.info(f"Exported equity CSV: {equity_file}")

# Example usage
if __name__ == "__main__":
    from python.data_loader import TickDataLoader
    from examples.basic_strategy_example import BasicRSIStrategy
    
    # Initialize
    loader = TickDataLoader("./data/processed/")
    engine = TestEngine(loader)
    
    # Test configuration
    test_config = {
        'symbol': 'EURUSD',
        'data_mode': 'realistic',
        'initial_balance': 100000.0,
        'position_size': 0.02,
        'debug_enabled': True
    }
    
    # Strategy parameters
    strategy_params = {
        'rsi_period': 14,
        'oversold_threshold': 30.0,
        'overbought_threshold': 70.0
    }
    
    # Run test
    results = engine.run_test(BasicRSIStrategy, strategy_params, test_config)
    
    # Export results
    engine.export_results(results, Path("./results/test_001/"))
    
    # Print summary
    metrics = results['performance_metrics']
    print(f"\n=== Test Results ===")
    print(f"Trades: {metrics.get('total_trades', 0)}")
    print(f"Win Rate: {metrics.get('win_rate', 0):.2%}")
    print(f"Sharpe Ratio: {metrics.get('sharpe_ratio', 0):.2f}")
    print(f"Max Drawdown: {metrics.get('max_drawdown', 0):.2%}")
