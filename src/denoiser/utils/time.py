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


def iso_utc(dt: datetime.datetime | None) -> str | None:
    """ISO-8601 with an explicit UTC offset, for values sent to a browser.

    The datetimes stored here are naive UTC, and ``isoformat()`` emits them with
    no zone at all. JavaScript's ``new Date()`` reads such a string as *local*
    time, so a check that ran seconds ago rendered as hours ago on any machine
    that is not on UTC.
    """
    if dt is None:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.UTC)
    return dt.isoformat()


def to_epoch_ms(dt: datetime.datetime) -> int:
    """Epoch milliseconds for a datetime, reading a naive value as UTC.

    ``utcnow()`` returns naive UTC, but ``datetime.timestamp()`` interprets a
    naive value as *local* time. On a machine east of UTC that silently moves
    the instant backwards — a query window built this way in IST landed 5½
    hours in the past and excluded everything just written.
    """
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=datetime.UTC)
    return int(dt.timestamp() * 1000)
