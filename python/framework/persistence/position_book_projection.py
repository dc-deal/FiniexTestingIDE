"""
FiniexTestingIDE - Position Book Projection (#355)

Translates an open Position into the plain note a carry-over can hold, and back.

The projection is deliberately FAITHFUL rather than minimal. A partial view would not fail
loudly — it would produce a closing trade record that looks complete and is not: the excursion
extrema (#389) would restart at zero, the submission slippage audit (#340) would be blank, the
entry executions would read as a position that never had any, and a PARTIALLY_CLOSED position
would come back as untouched. Every one of those is a report that lies quietly, which is worse
than a restore that refuses.

What is deliberately NOT carried: the running marks (current_price, unrealized_pnl, gross_pnl).
They are recomputed from the next tick, so writing them down would only create a second, older
answer to a question that already has a current one.
"""

from typing import List

from python.framework.trading_env.trading_fees import RestoredFee
from python.framework.types.persistence_types import (
    BrokerTradeCarryOver,
    PositionCarryOver,
    PositionFeeCarryOver,
    SubmissionCarryOver,
)
from python.framework.types.portfolio_types.portfolio_trade_record_types import EntryType
from python.framework.types.portfolio_types.portfolio_types import Position, PositionStatus
from python.framework.types.trading_env_types.broker_trade_types import BrokerTrade
from python.framework.types.trading_env_types.broker_types import FeeStatus, FeeType
from python.framework.types.trading_env_types.order_types import OrderDirection, OrderSide
from python.framework.types.trading_env_types.submission_metadata_types import SubmissionMetadata
from python.framework.utils.time_utils import parse_datetime


def position_to_carry_over(position: Position) -> PositionCarryOver:
    """
    Project one open position onto its carry-over note.

    Args:
        position: The open position to write down

    Returns:
        The note, ready for the carry-over payload
    """
    return PositionCarryOver(
        position_id=position.position_id,
        symbol=position.symbol,
        direction=position.direction.value,
        lots=position.lots,
        original_lots=position.original_lots,
        entry_price=position.entry_price,
        entry_time=position.entry_time.isoformat(),
        entry_type=position.entry_type.value,
        entry_tick_value=position.entry_tick_value,
        entry_bid=position.entry_bid,
        entry_ask=position.entry_ask,
        stop_loss=position.stop_loss,
        take_profit=position.take_profit,
        broker_ref=position.broker_ref,
        comment=position.comment,
        status=position.status.value,
        digits=position.digits,
        contract_size=position.contract_size,
        pip_size=position.pip_size,
        price_unit=position.price_unit,
        entry_tick_index=position.entry_tick_index,
        mae_pnl=position.mae_pnl,
        mfe_pnl=position.mfe_pnl,
        mae_price=position.mae_price,
        mfe_price=position.mfe_price,
        swap_accrued_until=(
            position.swap_accrued_until.isoformat() if position.swap_accrued_until else None),
        fees=[
            PositionFeeCarryOver(
                fee_type=fee.fee_type.value,
                status=fee.status.value,
                timestamp=fee.timestamp.isoformat(),
                cost=fee.cost,
            )
            for fee in position.fees
        ],
        entry_trades=[
            BrokerTradeCarryOver(
                trade_id=trade.trade_id,
                parent_broker_ref=trade.parent_broker_ref,
                order_id=trade.order_id,
                volume=trade.volume,
                price=trade.price,
                fee=trade.fee,
                fee_currency=trade.fee_currency,
                timestamp=trade.timestamp.isoformat(),
                side=trade.side.value,
                is_maker=trade.is_maker,
            )
            for trade in position.entry_trades
        ],
        entry_submission=SubmissionCarryOver(
            tick_mid_price=position.entry_submission.tick_mid_price,
            tick_time_msc=position.entry_submission.tick_time_msc,
        ),
    )


def carry_over_to_position(record: PositionCarryOver) -> Position:
    """
    Rebuild one open position from its carry-over note.

    The excursion extrema are assigned AFTER construction on purpose: Position.__post_init__
    seeds mae_price / mfe_price from entry_price unconditionally, so a value passed to the
    constructor would be discarded without a word.

    Args:
        record: The note written by an earlier session

    Returns:
        The position, as it stood when the note was written
    """
    position = Position(
        position_id=record.position_id,
        symbol=record.symbol,
        direction=OrderDirection(record.direction),
        lots=record.lots,
        original_lots=record.original_lots,
        entry_price=record.entry_price,
        entry_time=parse_datetime(record.entry_time),
        stop_loss=record.stop_loss,
        take_profit=record.take_profit,
        entry_type=EntryType(record.entry_type),
        comment=record.comment,
        broker_ref=record.broker_ref,
        entry_tick_value=record.entry_tick_value,
        entry_bid=record.entry_bid,
        entry_ask=record.entry_ask,
        digits=record.digits,
        contract_size=record.contract_size,
        pip_size=record.pip_size,
        price_unit=record.price_unit,
        entry_tick_index=record.entry_tick_index,
        swap_accrued_until=(
            parse_datetime(record.swap_accrued_until) if record.swap_accrued_until else None),
        fees=_restore_fees(record.fees),
        entry_trades=_restore_trades(record.entry_trades),
        entry_submission=SubmissionMetadata(
            tick_mid_price=record.entry_submission.tick_mid_price,
            tick_time_msc=record.entry_submission.tick_time_msc,
        ),
    )

    if record.status:
        position.status = PositionStatus(record.status)
    position.mae_pnl = record.mae_pnl
    position.mfe_pnl = record.mfe_pnl
    position.mae_price = record.mae_price or record.entry_price
    position.mfe_price = record.mfe_price or record.entry_price

    return position


def _restore_fees(records: List[PositionFeeCarryOver]) -> List[RestoredFee]:
    """
    Rebuild the settled fees of a restored position.

    Args:
        records: The fee notes

    Returns:
        Fee objects carrying the stored cost and the original fee type
    """
    return [
        RestoredFee(
            fee_type=FeeType(record.fee_type),
            status=FeeStatus(record.status),
            timestamp=parse_datetime(record.timestamp),
            cost=record.cost,
        )
        for record in records
    ]


def _restore_trades(records: List[BrokerTradeCarryOver]) -> List[BrokerTrade]:
    """
    Rebuild the atomic executions of a restored position's entry.

    Args:
        records: The execution notes

    Returns:
        BrokerTrade objects as they were recorded at fill time
    """
    return [
        BrokerTrade(
            trade_id=record.trade_id,
            parent_broker_ref=record.parent_broker_ref,
            order_id=record.order_id,
            volume=record.volume,
            price=record.price,
            fee=record.fee,
            fee_currency=record.fee_currency,
            timestamp=parse_datetime(record.timestamp),
            side=OrderSide(record.side),
            is_maker=record.is_maker,
        )
        for record in records
    ]
