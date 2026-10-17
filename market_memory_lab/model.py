"""Strict identifiers, UTC cutoffs, decimals and duplicate-free JSON."""
import json
import re
from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation

IDENT = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")


class MemoryErrorBase(Exception):
    code = "memory_error"


class ValidationError(MemoryErrorBase):
    code = "invalid_input"


class DatabaseUnavailable(MemoryErrorBase):
    code = "database_unavailable"


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def identifier(value):
    if not isinstance(value, str) or not IDENT.fullmatch(value):
        raise ValidationError("Identifier must contain 1–64 letters, digits, underscores or hyphens")
    return value


def timestamp(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z", value):
        raise ValidationError("Timestamp must be UTC YYYY-MM-DDTHH:MM:SSZ")
    try:
        parsed = datetime.strptime(value, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        raise ValidationError("Invalid UTC timestamp") from None
    if not 2000 <= parsed.year <= 2100:
        raise ValidationError("Timestamp outside MVP range 2000–2100")
    return int(parsed.timestamp())


def decimal_value(value):
    if not isinstance(value, str) or not re.fullmatch(r"-?\d{1,18}(?:\.\d{1,12})?", value):
        raise ValidationError("Metric values must be bounded decimal strings")
    try:
        result = Decimal(value)
    except InvalidOperation:
        raise ValidationError("Invalid decimal") from None
    return result


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValidationError("Duplicate JSON key")
        result[key] = value
    return result
