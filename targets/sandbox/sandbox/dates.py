"""Calendar helpers."""


def is_leap_year(year: int) -> bool:
    """Return True if ``year`` is a Gregorian leap year.

    The rule: divisible by 4, except centuries, unless divisible by 400.

    >>> is_leap_year(2024)
    True
    >>> is_leap_year(1900)
    False
    >>> is_leap_year(2000)
    True
    """
    # SEEDED BUG (deliberate, for the demo): the "unless divisible by 400" rule is missing.
    if year % 100 == 0:
        return False
    return year % 4 == 0


def days_in_month(year: int, month: int) -> int:
    """Return the number of days in ``month`` (1-12) of ``year``.

    Raise ValueError if ``month`` is outside 1-12.

    >>> days_in_month(2024, 2)
    29
    >>> days_in_month(2023, 2)
    28
    """
    if not 1 <= month <= 12:
        raise ValueError("month must be in 1..12")
    if month == 2:
        return 29 if is_leap_year(year) else 28
    if month in (4, 6, 9, 11):
        return 30
    return 31
