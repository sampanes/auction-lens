"""Coercion helpers that turn loosely typed input into strict domain values.

Listing data arrives from JSON, CSV, XML, and TOML, so any single field may be a
string, a number, a list, or absent. Every conversion lives here so that "what
does this raw value mean" has exactly one answer, and so that a failure names the
field the operator has to fix.
"""

from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

CENTS = Decimal("0.01")

# Sources write dimensions as "70x31x45" or with a U+00D7 multiplication sign.
DIMENSION_SEPARATOR = "x"
MULTIPLICATION_SIGN = "\u00d7"

# Several list-shaped fields also arrive as one pipe-delimited string.
LABEL_SEPARATOR = "|"


def is_absent(value: Any) -> bool:
    """Treat both an omitted key and an empty string as "not provided"."""
    return value is None or value == ""


def parse_decimal(value: Any, *, field_name: str) -> Decimal:
    """Read an exact non-negative decimal, keeping the precision of the source."""
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a number") from exc
    if amount < 0:
        raise ValueError(f"{field_name} cannot be negative")
    return amount


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
        return int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{field_name} must be a whole number") from exc


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
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


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
    dimensions = tuple(parse_decimal(item, field_name="package_dimensions_in") for item in values)
    if len(dimensions) not in {2, 3}:
        raise ValueError("package_dimensions_in must contain two or three dimensions")
    return dimensions
