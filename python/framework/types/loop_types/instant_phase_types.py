"""
FiniexTestingIDE - Instant Phases (#457)

The fixed phases every instant of the loop runs, in the order they run.
"""

from enum import Enum


class InstantPhase(Enum):
    """
    The phases one instant runs — the declaration order below IS the order, and the contract.

    Everything carrying the same stamp is one instant. The world moves first: prices and bars are
    applied before any pending order resolves, so a fill due in the same millisecond as a tick
    fills at that tick's price. Which events arrived decides only which phases have work; no kind
    of event goes before another. The dispatcher (#458) runs the phases; this enum names them.
    """
    MARKET_DATA = 'market_data'                 # prices and bars applied
    SIGNALS = 'signals'                         # signal snapshots up to the instant refreshed
    BROKER_RESOLUTIONS = 'broker_resolutions'   # fills and cancels due, at this instant's prices
    EVENT_DELIVERY = 'event_delivery'           # order events delivered to the decision hooks
    ALGO_PASS = 'algo_pass'                     # the tick pass, otherwise a heartbeat pass
    ORDER_EXECUTION = 'order_execution'         # what the decision sent
    TRAILING_DRAIN = 'trailing_drain'           # events the execution produced, delivered
