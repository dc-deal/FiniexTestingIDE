

def force_negative(input: float) -> float:
    return -abs(input) if input > 0 else 0.0


def force_positive(input: float) -> float:
    return +abs(input) if input > 0 else 0.0
