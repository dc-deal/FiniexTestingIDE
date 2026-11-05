"""
FiniexTestingIDE - Abstract Algo Mask
What an algo receives in place of an engine object (#653).

An algo meets the framework on two levels: the functional one — what it can call — and the contract
one — what it receives back. A mask is the contract: it shows a declared set of fields of one engine
object, computes what moves with the market when it is read, and never writes. The engine object
itself stays out of the algo's reach, so a field added to it later reaches no algo until a mask
declares it, and nothing an algo holds can change the engine's state.

What the mask cannot do in Python is make the engine object truly private: an algo that deliberately
reaches through the private reference still can. The project's rule against private access forbids
it; the mask protects against accidents and against fields leaking unreviewed.
"""

from typing import Any, Tuple


class AbstractAlgoMask:
    """
    A read-only view of one engine object, its fields declared as the properties of the subclass.

    A subclass declares each field it shows as a property; `get_mask_fields()` reads them from the
    class, so the positive list is the class itself and a test can hold its documentation to it.
    """

    __slots__ = ('_source',)

    def __init__(self, source: Any):
        """
        Wrap an engine object.

        Args:
            source: The engine object the mask shows
        """
        object.__setattr__(self, '_source', source)

    def __setattr__(self, name: str, value: Any) -> None:
        """
        Refuse every assignment — a mask is read-only.

        Args:
            name: The attribute an algo tried to set
            value: The value it tried to set
        """
        raise AttributeError(f'{type(self).__name__} is read-only: {name} cannot be set')

    def is_mask_of(self, source: Any) -> bool:
        """
        Whether this mask shows exactly this engine object.

        Args:
            source: An engine object

        Returns:
            True when it is the object behind this mask
        """
        return self._source is source

    @classmethod
    def get_mask_fields(cls) -> Tuple[str, ...]:
        """
        The fields this mask shows, in the order the class declares them.

        Returns:
            The names of the mask's properties, its own and its bases'
        """
        names = []
        for klass in reversed(cls.__mro__):
            for name, member in vars(klass).items():
                if isinstance(member, property) and name not in names:
                    names.append(name)
        return tuple(names)
