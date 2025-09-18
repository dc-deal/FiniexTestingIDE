# Tick-Signifikanz-Filter für High-Frequency-Performance

## Problem
Bei hoher Tick-Frequenz (>500 ticks/sec) können Indikator-Berechnungen nicht mithalten.

## Lösung: Signifikanz-basierte Filterung

### Grundkonzept
**Nicht jeder Tick rechtfertigt eine komplette Neuberechnung aller Indikatoren.**

```python
def is_significant_tick(self, current_tick, last_processed_tick):
    price_change = abs(current_tick.mid_price - last_processed_tick.mid_price)
    price_threshold = last_processed_tick.mid_price * self.significance_threshold
    
    return price_change >= price_threshold
```

### Signifikanz-Kriterien

#### 1. **Price-Based Significance**
- **Threshold**: 0.01% - 0.1% je nach Symbol-Volatilität
- **EURUSD**: 0.02% (2 Pips bei 1.1000)
- **GBPJPY**: 0.05% (5 Pips bei 100.00)

#### 2. **Volume-Based Significance**
```python
volume_spike = current_tick.volume > (avg_volume * 2.0)
```

#### 3. **Time-Based Fallback**
```python
time_since_last = current_tick.timestamp - last_processed_tick.timestamp
force_update = time_since_last > max_latency_ms
```

## Processing-Modi

### FULL MODE (Normal Load)
- **Bedingung**: <100 ticks/sec
- **Verhalten**: Jeden Tick verarbeiten
- **Latenz**: <1ms

### SMART MODE (Medium Load)  
- **Bedingung**: 100-500 ticks/sec
- **Verhalten**: Nur signifikante Ticks + Zeitfallback
- **Latenz**: <2ms
- **CPU-Reduktion**: ~60%

### LATCH MODE (High Load)
- **Bedingung**: >500 ticks/sec
- **Verhalten**: Nur bei Signifikanz ODER Max-Latenz erreicht
- **Latenz**: <5ms
- **CPU-Reduktion**: ~80%

## Implementierung

```python
class AdaptiveTickProcessor:
    def __init__(self):
        self.significance_threshold = 0.0002  # 0.02%
        self.max_latency_ms = 100
        self.last_processed_tick = None
        
    def should_process_tick(self, tick):
        if self.last_processed_tick is None:
            return True
            
        # Signifikanz-Check
        if self.is_significant_tick(tick):
            return True
            
        # Zeit-Fallback
        if self.time_since_last_process(tick) > self.max_latency_ms:
            return True
            
        return False
        
    def process_if_significant(self, tick):
        if self.should_process_tick(tick):
            result = self.run_full_calculation(tick)
            self.last_processed_tick = tick
            return result
        else:
            # Verwende letztes Ergebnis
            return self.last_signal
```

## Symbol-spezifische Thresholds

| Symbol  | Typical Spread | Significance Threshold | Max Latency |
|---------|---------------|----------------------|-------------|
| EURUSD  | 1-2 pips      | 0.015% (1.5 pips)   | 50ms        |
| GBPUSD  | 2-3 pips      | 0.020% (2.0 pips)   | 50ms        |
| USDJPY  | 1-2 pips      | 0.015% (1.5 pips)   | 50ms        |
| GBPJPY  | 3-5 pips      | 0.035% (3.5 pips)   | 100ms       |

## Performance-Gains

### Ohne Signifikanz-Filter
- **CPU-Last**: 95-100% bei >500 ticks/sec
- **Latenz**: 5-20ms (unakzeptabel)
- **Missed Signals**: Häufig durch Overload

### Mit Signifikanz-Filter
- **CPU-Last**: 20-40% bei >500 ticks/sec  
- **Latenz**: 1-3ms (akzeptabel)
- **Signal-Qualität**: 95%+ der wichtigen Bewegungen erfasst

## Adaptive Threshold-Anpassung

```python
class DynamicThresholdManager:
    def adjust_threshold_by_volatility(self, symbol):
        recent_volatility = self.calculate_recent_atr(symbol)
        base_threshold = self.base_thresholds[symbol]
        
        # Bei hoher Volatilität: Höhere Threshold
        volatility_multiplier = min(3.0, recent_volatility / average_volatility)
        
        return base_threshold * volatility_multiplier
```

## Risiken & Mitigation

### Risiko: Verpasste wichtige Micro-Bewegungen
**Mitigation**: Time-based Fallback garantiert maximale Latenz

### Risiko: Falsche Threshold-Kalibrierung  
**Mitigation**: Adaptive Anpassung basierend auf aktueller Markt-Volatilität

### Risiko: Signal-Degradation
**Mitigation**: A/B-Testing zwischen Full-Mode und Filtered-Mode für Performance-Validierung