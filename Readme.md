# FiniexTestingIDE

Finiex™ Testing IDE
© 2024 Frank Krätzig. All rights reserved.
Finiex™ is a trademark of Frank Krätzig.

**Professional Trading Strategy Testing & Development Environment**

> Revolutionary IDE-like platform for testing trading strategies with **IP protection**, **massive parallelization**, and **reproducible results**.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.11+](https://img.shields.io/badge/python-3.11+-blue.svg)](https://www.python.org/downloads/)
[![Status: MVP Development](https://img.shields.io/badge/status-MVP%20Development-orange.svg)]()

---

## 🚀 Vision

FiniexTestingIDE solves the fundamental problem of **trading strategy development**:

**"How do you test strategies fast, fair, and reproducibly - without revealing your IP?"**

### Core Features

✅ **Blackbox-API** - Strategies remain secret, testing stays transparent  
✅ **Massive Parallelization** - 1000+ scenarios simultaneously  
✅ **Reproducible Results** - Deterministic seeds & immutable snapshots  
✅ **Visual Debug System** - See every indicator, signal, and calculation  
✅ **Production Ready** - Seamless handover to live trading systems  

---

## 🏗️ Architecture

```
MQL5 Data Collection → JSON Export → Python Pipeline → Parquet Database → Testing IDE
```

**Why This Stack:**
- **MQL5**: Live tick data from any forex broker
- **Apache Arrow/Parquet**: Zero-copy performance for big datasets  
- **Python Multiprocessing**: True parallelism (no GIL limits)
- **Blackbox Framework**: IP protection + standardized interface

---

## 🎯 Quick Start

### Phase 1: Data Collection (2 Days)
```bash
# 1. Install MQL5 TickCollector in MetaTrader 5
cp mql5/TickCollector.mq5 [MetaTrader]/MQL5/Experts/

# 2. Run on EURUSD chart for 48 hours
# → Generates JSON tick data in C:/FinexData/

# 3. Expected output: 300-900MB raw tick data
```

### Phase 2: Python Pipeline (1 Week)
```bash
# 1. Setup environment
pip install -r requirements.txt

# 2. Convert JSON to Parquet
python python/tick_importer.py

# 3. Test data loading
python python/data_loader.py
```

### Phase 3: Your First Strategy
```python
from python.blackbox_framework import BlackboxBase, Signal

class MyStrategy(BlackboxBase):
    def get_parameter_schema(self):
        return {
            'rsi_period': {'type': 'int', 'default': 14, 'description': 'RSI Period'}
        }
    
    def on_tick(self, tick):
        # Your secret trading logic here
        rsi = self.indicators.rsi(self.price_history, self.parameters['rsi_period'])
        
        # Visual debug (only in development)
        self.add_line_point("rsi", rsi, tick.timestamp)
        
        if rsi < 30:
            return Signal("BUY", price=tick.ask)
        elif rsi > 70:
            return Signal("SELL", price=tick.bid)
        
        return Signal("FLAT")
```

---

## 📊 Performance Targets

| Metric | Target | Status |
|--------|---------|---------|
| **Time-to-First-Backtest** | < 30 min | 🟡 In Progress |
| **Parallel Scenarios** | 1000+ | 🟡 In Progress |
| **Determinism Rate** | ≥ 99% | 🟡 In Progress |
| **Data Compression** | 10:1 ratio | ✅ Achieved |

---

## 📁 Project Structure

- **`docs/`** - Complete documentation
- **`mql5/`** - MetaTrader 5 data collectors
- **`python/`** - Core framework & pipeline
- **`data/`** - Tick data storage (gitignored)
- **`examples/`** - Sample strategies & data
- **`scripts/`** - Utility scripts

---

## 🛣️ Roadmap

### 2025 Q1 - MVP ✅
- [x] Blackbox Framework Design
- [x] MQL5 Data Pipeline
- [ ] Multi-Process Testing Engine
- [ ] Basic Web UI

### 2025 Q2 - Scale-Up
- [ ] Distributed Computing
- [ ] Advanced Visual Debug
- [ ] Parameter Dependencies UI
- [ ] Performance Optimization

### 2025 Q3 - Production
- [ ] Strategy Obfuscation/Compilation
- [ ] Enterprise Security
- [ ] SaaS Platform Beta
- [ ] Live Trading Integration

---

## 🤝 Contributing

We welcome contributions! Please see [CONTRIBUTING.md](docs/contributing.md) for guidelines.

### Development Setup
```bash
git clone https://github.com/dc-deal/FiniexTestingIDE.git
cd FiniexTestingIDE
pip install -r requirements.txt
python scripts/setup.py
```

---

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

---

## 📞 Contact

**Project Maintainer:** [dc-deal](https://github.com/dc-deal)

**Issues:** [GitHub Issues](https://github.com/dc-deal/FiniexTestingIDE/issues)

---

*FiniexTestingIDE - Revolutionizing Trading Strategy Development* 🚀
