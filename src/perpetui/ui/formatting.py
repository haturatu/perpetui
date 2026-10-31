from decimal import Decimal


def price(value: Decimal | None) -> str:
    if value is None:
        return "—"
    magnitude = abs(value)
    places = (
        2 if magnitude >= 100 else 4 if magnitude >= 1 else 6 if magnitude >= Decimal(".01") else 8
    )
    return f"{value:,.{places}f}"


def compact(value: Decimal) -> str:
    for suffix, divisor in (("B", 10**9), ("M", 10**6), ("K", 10**3)):
        if abs(value) >= divisor:
            return f"{value / divisor:.2f}{suffix}"
    return f"{value:,.2f}"
