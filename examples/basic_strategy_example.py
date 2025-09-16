"""
FiniexTestingIDE - Basic Strategy Example
Demonstrates how to create a simple RSI-based trading strategy
"""

from python.blackbox_framework import BlackboxBase, Signal, Tick

class BasicRSIStrategy(BlackboxBase):
    """
    Simple RSI-based mean reversion strategy
    
    Logic:
    - Buy when RSI < 30 (oversold)
    - Sell when RSI > 70 (overbought) 
    - Hold otherwise
    """
    
    def get_parameter_schema(self):
        """Define strategy parameters"""
        return {
            'rsi_period': {
                'type': 'int',
                'default': 14,
                'min_val': 5,
                'max_val': 50,
                'description': 'RSI calculation period',
                'category': 'Technical Indicators'
            },
            'oversold_threshold': {
                'type': 'float',
                'default': 30.0,
                'min_val': 10.0,
                'max_val': 40.0,
                'description': 'RSI oversold threshold (buy signal)',
                'category': 'Signal Thresholds'
            },
            'overbought_threshold': {
                'type': 'float',
                'default': 70.0,
                'min_val': 60.0,
                'max_val': 90.0,
                'description': 'RSI overbought threshold (sell signal)',
                'category': 'Signal Thresholds'
            }
        }
    
    def on_tick(self, tick: Tick) -> Signal:
        """Main strategy logic executed on every tick"""
        
        # Add current price to history
        self.price_history.append(tick.mid_price)
        
        # Need enough history for RSI calculation
        required_bars = self.parameters['rsi_period'] + 1
        if len(self.price_history) < required_bars:
            return Signal("FLAT", comment="Waiting for sufficient history")
        
        # Calculate RSI
        rsi = self.indicators.rsi(
            list(self.price_history), 
            self.parameters['rsi_period']
        )
        
        if rsi is None:
            return Signal("FLAT", comment="RSI calculation failed")
        
        # Visual debug output (only in development mode)
        self.add_line_point("rsi", rsi, tick.timestamp)
        self.add_line_point("oversold_line", self.parameters['oversold_threshold'], tick.timestamp)
        self.add_line_point("overbought_line", self.parameters['overbought_threshold'], tick.timestamp)
        
        # Trading logic
        if rsi <= self.parameters['oversold_threshold']:
            # Oversold - Buy signal
            self.add_arrow("up", tick.mid_price, tick.timestamp, f"RSI Buy ({rsi:.1f})")
            
            return Signal(
                action="BUY",
                price=tick.ask,
                quantity=1.0,
                confidence=min(1.0, (self.parameters['oversold_threshold'] - rsi) / 10.0),
                comment=f"RSI oversold: {rsi:.2f}"
            )
            
        elif rsi >= self.parameters['overbought_threshold']:
            # Overbought - Sell signal  
            self.add_arrow("down", tick.mid_price, tick.timestamp, f"RSI Sell ({rsi:.1f})")
            
            return Signal(
                action="SELL", 
                price=tick.bid,
                quantity=1.0,
                confidence=min(1.0, (rsi - self.parameters['overbought_threshold']) / 10.0),
                comment=f"RSI overbought: {rsi:.2f}"
            )
            
        else:
            # Neutral zone - Hold
            return Signal(
                action="FLAT",
                comment=f"RSI neutral: {rsi:.2f}"
            )

# Example usage
if __name__ == "__main__":
    # Create strategy instance
    strategy = BasicRSIStrategy(debug_enabled=True)
    
    # Set parameters
    params = {
        'rsi_period': 14,
        'oversold_threshold': 30.0, 
        'overbought_threshold': 70.0
    }
    strategy.set_parameters(params)
    
    # Print parameter schema
    print("Parameter Schema:")
    schema = strategy.get_parameter_schema()
    for name, config in schema.items():
        print(f"  {name}: {config['description']} (default: {config['default']})")
    
    print(f"\nStrategy ready with {len(strategy.parameters)} parameters")