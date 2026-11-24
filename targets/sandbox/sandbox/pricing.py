"""Pricing helpers."""


def apply_discount(price: float, percent: float) -> float:
    """Return ``price`` reduced by ``percent`` (0-100), rounded to 2 decimals.

    Raise ValueError if ``percent`` is outside 0-100.
    """
    if not 0 <= percent <= 100:
        raise ValueError("percent must be in 0..100")
    return round(price * (1 - percent / 100), 2)


def total_with_tax(subtotal: float, rate: float) -> float:
    """Return ``subtotal`` plus tax at ``rate`` (e.g. 0.08), rounded to 2 decimals.

    Raise ValueError if ``subtotal`` or ``rate`` is negative.
    """
    if subtotal < 0 or rate < 0:
        raise ValueError("subtotal and rate must be non-negative")
    return round(subtotal * (1 + rate), 2)


if __name__ == "__main__":
    print(total_with_tax(100.0, 0.08))
