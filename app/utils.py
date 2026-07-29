from datetime import datetime, timezone


def utc_now():
    return datetime.now(timezone.utc)


def utc_isoformat(value):
    """Serialize a stored timestamp as an explicit UTC ISO-8601 value.

    SQLAlchemy's ``DateTime`` columns are intentionally kept timezone-naive
    for compatibility with the existing MySQL schema.  Values read back from
    those columns therefore lose their ``tzinfo`` even though they represent
    UTC.  Emitting a trailing ``Z`` prevents browsers and other clients from
    incorrectly treating the timestamp as local time.
    """
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    else:
        value = value.astimezone(timezone.utc)
    return value.isoformat().replace("+00:00", "Z")
