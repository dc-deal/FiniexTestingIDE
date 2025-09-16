# FiniexTestingIDE - Blackbox Framework
# ====================================
# Vollständiges MVP-Framework für Trading-Strategien als Blackboxes
# Autor: Claude/Anthropic
# Version: 1.0
# Datum: Januar 2025

from abc import ABC, abstractmethod
from dataclasses import dataclass, asdict
from typing import Dict, List, Any, Optional, Tuple
import json
import time
import hashlib
from collections import deque
import numpy as np
import logging
from multiprocessing import ProcessPoolExecutor, cpu_count
import warnings

# Logging Setup
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ===========================================
# 1. CORE DATA STRUCTURES
# ===========================================

@dataclass
class Tick:
    """Standard Tick-Datenstruktur für alle Instrumente"""
    symbol: str
    timestamp: str  # ISO Format: "2024-01-15T14:23:45.123456Z"
    bid: float
    ask: float
    volume: float
    spread_points: float
    
    @property
    def mid_price(self) -> float:
        """Mittlerer Preis zwischen Bid/Ask"""
        return (self.bid + self.ask) / 2.0
    
    @property
    def spread_pct(self) -> float:
        """Spread als Prozent vom Mid-Price"""
        if self.mid_price == 0:
            return 0.0
        return (self.ask - self.bid) / self.mid_price * 100

@dataclass
class Parameter:
    """Parameter-Definition mit Validierung und Metadaten"""
    name: str
    value: Any
    param_type: str  # "int", "float", "bool", "str"
    min_val: Optional[float] = None
    max_val: Optional[float] = None
    default_val: Any = None
    description: str = ""
    category: str = "General"
    
    def validate(self) -> bool:
        """Validiert Parameter-Wert gegen Constraints"""
        try:
            if self.param_type == "int":
                self.value = int(self.value)
            elif self.param_type == "float":
                self.value = float(self.value)
            elif self.param_type == "bool":
                self.value = bool(self.value)
            elif self.param_type == "str":
                self.value = str(self.value)
                
            # Range-Check für numerische Werte
            if self.param_type in ["int", "float"]:
                if self.min_val is not None and self.value < self.min_val:
                    raise ValueError(f"{self.name}: {self.value} < {self.min_val}")
                if self.max_val is not None and self.value > self.max_val:
                    raise ValueError(f"{self.name}: {self.value} > {self.max_val}")
                    
            return True
        except (ValueError, TypeError) as e:
            logger.error(f"Parameter validation failed for {self.name}: {e}")
            return False

@dataclass
class Signal:
    """Trading-Signal Output der Blackbox"""
    action: str  # "BUY", "SELL", "FLAT"
    price: Optional[float] = None
    quantity: float = 1.0
    stop_loss: Optional[float] = None
    take_profit: Optional[float] = None
    confidence: float = 1.0
    timestamp: Optional[str] = None
    comment: str = ""
    
    def __post_init__(self):
        if self.timestamp is None:
            self.timestamp = time.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            
    def is_valid(self) -> bool:
        """Validiert Signal-Struktur"""
        return self.action in ["BUY", "SELL", "FLAT"] and 0 <= self.confidence <= 1

@dataclass
class VisualElement:
    """Debug-Visual Element für Chart-Rendering"""
    element_type: str  # "line_point", "arrow", "zone", "text"
    name: str
    timestamp: str
    data: Dict[str, Any]
    color: str = "blue"
    style: str = "solid"
    
    def to_dict(self) -> Dict[str, Any]:
        """Serialisierung für JSON-Export"""
        return asdict(self)

# ===========================================
# 2. TECHNICAL
