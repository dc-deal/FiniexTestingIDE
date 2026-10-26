

def force_negative(input: float) -> float:
    return -abs(input) if input > 0 else 0.0


def force_positive(input: float) -> float:
    return +abs(input) if input > 0 else 0.0


def negated_magnitude(value: float) -> float:
    """
    The magnitude of a value, negated whichever sign it was stored with, and zero as 0.0.

    For a decline, which the ledger holds either way round: a magnitude on one column, a negative
    on another, rows of both ages in one store. Zero stays 0.0 — `-abs(0.0)` is -0.0, which prints
    as "-0.00", a decline nobody had.

    Args:
        value: The value, either sign

    Returns:
        Its negated magnitude, or 0.0 for zero
    """
    return -abs(value) if value else 0.0
