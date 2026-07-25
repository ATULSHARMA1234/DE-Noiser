"""Time helpers.

Drop-in replacements for the deprecated ``datetime.utcnow`` /
``datetime.utcfromtimestamp`` (removed in a future Python). These return
*naive* UTC datetimes so existing comparisons, ``.isoformat()`` output, and
SQLAlchemy ``DateTime`` columns keep their exact previous behaviour.
"""

import datetime


def utcnow() -> datetime.datetime:
    """Naive UTC now (drop-in for ``datetime.datetime.utcnow()``)."""
    return datetime.datetime.now(datetime.UTC).replace(tzinfo=None)


def utcfromtimestamp(ts: float) -> datetime.datetime:
    """Naive UTC from a POSIX timestamp (drop-in for ``utcfromtimestamp``)."""
    return datetime.datetime.fromtimestamp(ts, datetime.UTC).replace(tzinfo=None)
