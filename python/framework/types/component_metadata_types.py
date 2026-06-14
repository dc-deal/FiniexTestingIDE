"""
FiniexTestingIDE - Component Metadata

Author-declared metadata for a worker or decision logic. Complements the
automatic config_fingerprint (which captures the exact parameter set) with
semantic intent: a human-maintained version, a documentation pointer, and an
advisory market/instrument fit. Surfaced in run summaries; the recommended
lists drive a soft (non-blocking) market-fit warning at pre-flight.
"""

from dataclasses import dataclass
from typing import Optional, Tuple


@dataclass(frozen=True)
class ComponentMetadata:
    """
    Author-declared metadata for a worker or decision logic.

    Args:
        version: Semantic version string (author-maintained)
        doc_link: Relative path to the component's main doc (informational pointer)
        recommended_markets: Market types the component is designed for (advisory;
            empty = no recommendation = no warning)
        recommended_instruments: Symbols the component is designed for (advisory;
            empty = no recommendation = no warning)
    """
    version: str = '0.0.0'
    doc_link: Optional[str] = None
    recommended_markets: Tuple[str, ...] = ()
    recommended_instruments: Tuple[str, ...] = ()
