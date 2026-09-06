"""The vocabulary of what a value is allowed to be.

Two kinds of input reach this project. Listing data arrives from JSON, CSV, and
XML, where any field may be a string, a number, or absent, so it has to be
coerced. Configuration arrives from TOML, which is already typed, so it only has
to be checked. Both use the words below, which is why an operator sees the same
sentence for the same mistake no matter which file it was in.

Every function names the field it is unhappy about, because the person reading
the error is the person who has to go and edit that field.
"""

from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, TypeVar

CENTS = Decimal("0.01")

# Sources write dimensions as "70x31x45" or with a U+00D7 multiplication sign.
DIMENSION_SEPARATOR = "x"
MULTIPLICATION_SIGN = "\u00d7"

# Several list-shaped fields also arrive as one pipe-delimited string.
LABEL_SEPARATOR = "|"

Number = TypeVar("Number", int, Decimal)


# --------------------------------------------------------------------------
# Requirements. These check a value that is already the right type, and return
# it so they can be chained onto a parse or used alone in a __post_init__.
# --------------------------------------------------------------------------


def require_finite(value: Decimal, *, field_name: str) -> Decimal:
    """Reject the infinities and NaN that Decimal accepts but arithmetic cannot."""
    if not value.is_finite():
        raise ValueError(f"{field_name} must be a finite number")
    return value


def require_not_negative(value: Number, *, field_name: str) -> Number:
    """For a quantity whose meaning reverses below zero, such as a fee."""
    _require_finite_if_decimal(value, field_name=field_name)
    if value < 0:
        raise ValueError(f"{field_name} cannot be negative")
    return value


def require_at_least(value: Number, minimum: Number, *, field_name: str) -> Number:
    _require_finite_if_decimal(value, field_name=field_name)
    if value < minimum:
        raise ValueError(f"{field_name} must be at least {minimum}")
    return value


def require_at_most(value: Number, maximum: Number, *, field_name: str) -> Number:
    _require_finite_if_decimal(value, field_name=field_name)
    if value > maximum:
        raise ValueError(f"{field_name} cannot exceed {maximum}")
    return value


def require_within(value: Number, *, low: Number, high: Number, field_name: str) -> Number:
    _require_finite_if_decimal(value, field_name=field_name)
    if not low <= value <= high:
        raise ValueError(f"{field_name} must be between {low} and {high}")
    return value


def _require_finite_if_decimal(value: Number, *, field_name: str) -> None:
    """Apply Decimal's extra validity rule before making an ordered comparison."""
    if isinstance(value, Decimal):
        require_finite(value, field_name=field_name)


# --------------------------------------------------------------------------
# Coercions. These turn a loosely typed value from a listing file into a
# strict one, applying the requirements above on the way.
# --------------------------------------------------------------------------


def is_absent(value: Any) -> bool:
    """Treat an omitted key or whitespace-only text as "not provided"."""
    return value is None or (isinstance(value, str) and not value.strip())


def parse_decimal(value: Any, *, field_name: str) -> Decimal:
    """Read an exact non-negative decimal, keeping the precision of the source."""
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a number") from exc
    return require_not_negative(amount, field_name=field_name)


def parse_money(value: Any, *, field_name: str) -> Decimal:
    """Read a currency amount, rounded to cents rather than to binary floats."""
    return parse_decimal(value, field_name=field_name).quantize(CENTS)


def parse_optional_money(value: Any, *, field_name: str) -> Decimal | None:
    return None if is_absent(value) else parse_money(value, field_name=field_name)


def parse_optional_decimal(value: Any, *, field_name: str) -> Decimal | None:
    return None if is_absent(value) else parse_decimal(value, field_name=field_name)


def parse_whole_number(value: Any, *, field_name: str) -> int:
    """Read a count, such as how many bids a listing has received."""
    if is_absent(value):
        return 0
    try:
        number = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a whole number") from exc
    if not number.is_finite() or number != number.to_integral_value():
        raise ValueError(f"{field_name} must be a whole number")
    return int(require_not_negative(number, field_name=field_name))


def parse_rate(value: Any, *, field_name: str) -> Decimal:
    """Read a proportion such as a 0.15 buyer premium; a rate is not cents."""
    return parse_decimal(value, field_name=field_name)


def parse_optional_rate(value: Any, *, field_name: str) -> Decimal | None:
    return None if is_absent(value) else parse_rate(value, field_name=field_name)


def parse_utc_datetime(value: Any, *, field_name: str = "timestamp") -> datetime | None:
    """Accept ISO-8601 text with or without a zone and normalize it to UTC."""
    if is_absent(value):
        return None
    text = str(value).strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"{field_name} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def parse_labels(value: Any) -> tuple[str, ...]:
    """Normalize a list or delimited string into sorted, lowercase labels.

    Conditions and loading assistance are both free-text vocabularies matched
    case-insensitively by rules, so both are lowercased and sorted on the way in.
    """
    if is_absent(value):
        return ()
    values = value if isinstance(value, list) else str(value).split(LABEL_SEPARATOR)
    return tuple(sorted({str(item).strip().lower() for item in values if str(item).strip()}))


def parse_dimensions(value: Any) -> tuple[Decimal, ...]:
    """Read two or three package dimensions in inches, in the given order.

    Dimensions keep the precision they were written with. Rounding them to
    cents the way money is rounded would print "70.00 in" in a report.
    """
    if is_absent(value):
        return ()
    if isinstance(value, list):
        values = value
    else:
        text = str(value).lower().replace(MULTIPLICATION_SIGN, DIMENSION_SEPARATOR)
        values = text.split(DIMENSION_SEPARATOR)
    dimensions = tuple(
        parse_decimal(item, field_name="package_dimensions_in") for item in values
    )
    if len(dimensions) not in {2, 3}:
        raise ValueError("package_dimensions_in must contain two or three dimensions")
    return dimensions
