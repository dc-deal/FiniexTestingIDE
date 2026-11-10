"""
What a venue itself records, in words of our own — the trades it published, and the entries of an
account's ledger.

A venue answers both in its own shapes: its pair names, its asset codes, its entry types. The
readers that speak one venue's API turn the answer into these types, so the code that judges a
run against the venue's record never names a venue.
"""

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Optional


@dataclass(frozen=True)
class VenueTrade:
    """
    One trade a venue published for a symbol.

    Args:
        trade_id: The venue's number of the trade, counted per symbol
        executed_at: When it executed, as the venue stamps it
        price: Its price
        volume: Its volume in the base currency
    """
    trade_id: int
    executed_at: datetime
    price: float
    volume: float


class VenueLedgerEntryKind(StrEnum):
    """
    What moved an account's balance — one entry of the venue's ledger.

    TRADE: one leg of a trade, which belongs to an order
    CONVERSION: one leg of a conversion the venue made outside its order book — no order
    DEPOSIT: money or coins paid in
    WITHDRAWAL: money or coins paid out
    TRANSFER: a move between the account and another of the same owner — a wallet, a sub-account
    EARN: a reward the account received — staking, a dividend
    ADJUSTMENT: a correction or credit the venue booked
    OTHER: an entry type no other kind covers, or one the reader does not know
    """
    TRADE = 'trade'
    CONVERSION = 'conversion'
    DEPOSIT = 'deposit'
    WITHDRAWAL = 'withdrawal'
    TRANSFER = 'transfer'
    EARN = 'earn'
    ADJUSTMENT = 'adjustment'
    OTHER = 'other'

    def may_belong_to_an_order(self) -> bool:
        """
        Whether an entry of this kind can be the leg of an order.

        Returns:
            True for a trade's leg only — every other kind moves the account without an order
        """
        return self is VenueLedgerEntryKind.TRADE


@dataclass(frozen=True)
class VenueLedgerEntry:
    """
    One entry of an account's ledger.

    Args:
        entry_id: The venue's id of the entry
        venue_ref: The venue's reference of what the entry books — for a trade's leg, the trade
        executed_at: When the venue booked it
        kind: What moved the balance
        venue_type: The entry's type as the venue names it, kept as it was answered
        venue_subtype: Its subtype as the venue names it; '' where it has none
        asset: The currency it moved, in our code ('ETH', 'USD') where the reader knows it, else the
            venue's own code
        amount: What it moved, signed — positive into the account
        fee: The fee the venue charged on it, in the same currency
        order_ref: The venue's handle of the order a trade's leg belongs to — the handle our order
            events carry; None for every other kind, and for a leg the venue named no order for
    """
    entry_id: str
    venue_ref: str
    executed_at: datetime
    kind: VenueLedgerEntryKind
    venue_type: str
    venue_subtype: str
    asset: str
    amount: float
    fee: float
    order_ref: Optional[str] = None

    def get_balance_change(self) -> float:
        """
        What the entry did to its currency's balance.

        Returns:
            The amount less the fee — measured on Kraken: a conversion of 750.00 EUR received
            826.84 USD with a fee of 8.19, and the balance rose by 818.65
        """
        return self.amount - self.fee
