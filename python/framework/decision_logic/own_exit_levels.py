"""
FiniexTestingIDE - Own Exit Levels (#625)

A decision logic's own stop and target per position, checked on every tick.

A venue holds a stop or a target for a position only where its API takes one. Kraken's takes
neither — no level attached to an order, no bracket, no one-cancels-other (its API team,
2026-10-27) — and the framework does not rebuild what a venue lacks. So a logic that wants to
leave a position at a level checks the level itself and closes through its own close_position.
This is that check, written once, so a logic says WHERE its exits are and not how they fire.

What it is not: an order. Nothing rests at the venue, so a position is unprotected while the
process is not running — the same as every exit a strategy decides for itself.
"""

from typing import Any, Dict, List, Optional, Set, Tuple

from python.framework.logging.scenario_logger import ScenarioLogger
from python.framework.trading_env.decision_trading_api import DecisionTradingApi
from python.framework.types.market_types.market_data_types import TickData
from python.framework.types.portfolio_types.portfolio_trade_record_types import CloseReason
from python.framework.types.portfolio_types.portfolio_types import Position
from python.framework.types.trading_env_types.order_types import OrderDirection, OrderResult
from python.framework.types.trading_env_types.own_exit_types import OwnExit, PositionExitLevels
from python.framework.utils.trading_math.price_trigger import is_limit_reached, is_stop_reached


class OwnExitLevels:
    """
    A decision logic's own stop and target per position.

    Levels are set absolutely with `set_levels` — under the ENTRY ORDER's id where the logic
    decides them at submission, because an entry's order id becomes its position's id — or
    derived from the entry price and the seed distances the first time a position without
    levels appears: a fill whose order was not armed, a position read back after a restart.
    `move_stop` only tightens. `enforce` closes a position once the market reaches its stop or
    its target — the stop first, as a venue's own check would — through the logic's own
    close_position, and never while a close for it is already on its way.
    """

    def __init__(
        self,
        stop_distance: Optional[float] = None,
        target_distance: Optional[float] = None,
        logger: Optional[ScenarioLogger] = None,
    ):
        """
        Start with no levels.

        Args:
            stop_distance: Price distance from the entry a position without levels gets its
                stop at; None or not positive gives it none
            target_distance: The same for its target
            logger: Where reached levels and refused closes are logged; None logs nothing
        """
        self._levels: Dict[str, PositionExitLevels] = {}
        self._seen_open: Set[str] = set()
        self._stop_distance = stop_distance
        self._target_distance = target_distance
        self._logger = logger

    def set_seed_distances(
        self,
        stop_distance: Optional[float],
        target_distance: Optional[float],
    ) -> None:
        """
        Change the distances a position without levels is given — for a logic whose distances
        follow the market rather than a constant.

        Args:
            stop_distance: Price distance of the stop from the entry; None or not positive gives none
            target_distance: The same for the target
        """
        self._stop_distance = stop_distance
        self._target_distance = target_distance

    def set_levels(
        self,
        position_id: str,
        stop: Optional[float] = None,
        target: Optional[float] = None,
    ) -> None:
        """
        Set a position's stop and target, replacing whatever it had.

        Args:
            position_id: The position — or the entry order whose fill will open it
            stop: The stop, None for none
            target: The target, None for none
        """
        self._levels[position_id] = PositionExitLevels(stop=stop, target=target)

    def get_levels(self, position_id: str) -> Optional[PositionExitLevels]:
        """
        A position's levels.

        Args:
            position_id: The position

        Returns:
            Its levels, or None when it has none yet
        """
        return self._levels.get(position_id)

    def move_stop(self, position_id: str, direction: OrderDirection, stop: float) -> bool:
        """
        Move a position's stop toward the market — never away from it.

        Args:
            position_id: The position
            direction: Its direction — a long's stop only rises, a short's only falls
            stop: The new stop

        Returns:
            True when the stop moved, False when it would have loosened
        """
        levels = self._levels.setdefault(position_id, PositionExitLevels())
        if levels.stop is not None:
            tighter = stop > levels.stop if direction == OrderDirection.LONG else stop < levels.stop
            if not tighter:
                return False
        levels.stop = stop
        return True

    def forget(self, position_id: str) -> None:
        """
        Drop the levels of an entry order that will not fill — cancelled or refused.

        A position's levels need no forgetting: they go when the position is gone.

        Args:
            position_id: The position or entry order
        """
        self._levels.pop(position_id, None)
        self._seen_open.discard(position_id)

    def due(self, positions: List[Position], bid: float, ask: float) -> List[Tuple[str, OwnExit]]:
        """
        Which positions the quote has taken to their stop or target.

        Gives a position without levels its seed levels first, and drops the levels of a
        position seen before and gone now.

        Args:
            positions: The open positions of the quote's symbol
            bid: Current bid
            ask: Current ask

        Returns:
            (position id, the exit) for each position whose stop or target is reached
        """
        open_ids: Set[str] = set()
        exits: List[Tuple[str, OwnExit]] = []
        for position in positions:
            position_id = position.position_id
            open_ids.add(position_id)
            levels = self._levels.get(position_id) or self._seed(position)
            if levels is None:
                continue
            # Closing a long sells, closing a short buys — the levels are the closing order's
            closing = (OrderDirection.SHORT if position.direction == OrderDirection.LONG
                       else OrderDirection.LONG)
            if levels.stop is not None and is_stop_reached(closing, levels.stop, bid, ask):
                exits.append((position_id, OwnExit(CloseReason.SL_TRIGGERED, levels.stop)))
            elif levels.target is not None and is_limit_reached(closing, levels.target, bid, ask):
                exits.append((position_id, OwnExit(CloseReason.TP_TRIGGERED, levels.target)))
        for closed_id in self._seen_open - open_ids:
            self._levels.pop(closed_id, None)
        self._seen_open = open_ids
        return exits

    def enforce(self, trading_api: DecisionTradingApi, tick: TickData) -> List[OrderResult]:
        """
        Close every position of the tick's symbol whose stop or target the tick reached.

        A position whose close is already on its way is left alone: the close in flight is the
        exit, and a second request would only join it.

        Args:
            trading_api: The logic's trading API
            tick: The tick to check against

        Returns:
            The answer to each close sent
        """
        results: List[OrderResult] = []
        positions = trading_api.get_open_positions(tick.symbol)
        for position_id, own_exit in self.due(positions, tick.bid, tick.ask):
            if trading_api.is_pending_close(position_id):
                continue
            if self._logger is not None:
                which = ('🛑 Own stop' if own_exit.reason == CloseReason.SL_TRIGGERED
                         else '🎯 Own target')
                self._logger.info(
                    f'{which} reached: {position_id} @ {own_exit.level:.5f} '
                    f'(bid={tick.bid:.5f}, ask={tick.ask:.5f}) — closing')
            result = trading_api.close_position(position_id, exit=own_exit)
            if result.is_refused and self._logger is not None:
                self._logger.error(
                    f'❗ Own exit close REFUSED for {position_id} ({own_exit.reason.value} @ '
                    f'{own_exit.level:.5f}): {result.rejection_message}. The position is still '
                    f'open; the close is sent again on the next tick that reaches the level.')
            results.append(result)
        return results

    def snapshot(self) -> Dict[str, Any]:
        """
        The levels as JSON primitives, for the algo state persistence.

        Returns:
            position id → {'stop': …, 'target': …}
        """
        return {position_id: {'stop': levels.stop, 'target': levels.target}
                for position_id, levels in self._levels.items()}

    def restore(self, snapshot: Dict[str, Any], open_position_ids: Optional[Set[str]] = None) -> int:
        """
        Take back levels a snapshot kept — only for positions the framework restored.

        A level whose position did not come back is dropped: the positions are the framework's,
        and a logic never assumes one from its own memory. A position that came back without a
        level here is given its seed levels on the first check.

        Args:
            snapshot: What `snapshot` returned
            open_position_ids: The positions open now; None takes every level back

        Returns:
            How many positions got their levels back
        """
        restored = 0
        for position_id, levels in snapshot.items():
            if open_position_ids is not None and position_id not in open_position_ids:
                continue
            self._levels[position_id] = PositionExitLevels(
                stop=levels.get('stop'), target=levels.get('target'))
            restored += 1
        return restored

    def _seed(self, position: Position) -> Optional[PositionExitLevels]:
        """
        Give a position without levels the ones its entry price and the seed distances make.

        Args:
            position: The position

        Returns:
            Its new levels, or None when no seed distance is set
        """
        stop_distance = self._stop_distance if self._stop_distance and self._stop_distance > 0 else None
        target_distance = (self._target_distance
                           if self._target_distance and self._target_distance > 0 else None)
        if stop_distance is None and target_distance is None:
            return None
        sign = 1.0 if position.direction == OrderDirection.LONG else -1.0
        levels = PositionExitLevels(
            stop=position.entry_price - sign * stop_distance if stop_distance else None,
            target=position.entry_price + sign * target_distance if target_distance else None,
        )
        self._levels[position.position_id] = levels
        return levels
