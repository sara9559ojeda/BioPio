"""Time helpers. Colombia has no daylight saving time, so a fixed offset is exact."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta, timezone

from google.protobuf.timestamp_pb2 import Timestamp

COLOMBIA_TZ = timezone(timedelta(hours=-5), "COT")


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def today_local() -> date:
    return now_utc().astimezone(COLOMBIA_TZ).date()


def to_timestamp(value: datetime | date) -> Timestamp:
    if not isinstance(value, datetime):
        value = datetime.combine(value, time.min, tzinfo=COLOMBIA_TZ)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    ts = Timestamp()
    ts.FromDatetime(value.astimezone(timezone.utc))
    return ts


def from_timestamp(ts: Timestamp) -> datetime:
    return ts.ToDatetime(tzinfo=timezone.utc)


def has_timestamp(ts: Timestamp) -> bool:
    return ts.seconds != 0 or ts.nanos != 0


def local_date(value: datetime) -> date:
    return value.astimezone(COLOMBIA_TZ).date()


def week_start(day: date) -> date:
    """Monday of the ISO week containing `day`."""
    return day - timedelta(days=day.weekday())


def iso(day: date) -> str:
    return day.isoformat()


def parse_day(text: str) -> date:
    return date.fromisoformat(text)


def parse_datetime(text: str) -> datetime:
    value = datetime.fromisoformat(text)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value
