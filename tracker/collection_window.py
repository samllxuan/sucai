"""Calendar windows for newly launched ads, separate from delivery-date filters."""
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

WINDOWS = (0, 7, 14, 30)
SORTS = ('newest', 'default')


def launch_window(days, now=None):
    if days not in WINDOWS:
        raise ValueError('上线时间范围无效')
    if not days:
        return None, None
    today = (now or datetime.now(timezone.utc)).astimezone(ZoneInfo('Asia/Shanghai')).date()
    return (today - timedelta(days=days - 1)).isoformat(), today.isoformat()


def launch_date(record):
    value = record.get('start_date')
    if value is None or isinstance(value, bool):
        return None
    try:
        if isinstance(value, (int, float)) or (isinstance(value, str) and value.isdigit()):
            dt = datetime.fromtimestamp(float(value), timezone.utc)
        else:
            dt = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
        # Match the calendar start date returned by Meta, not our collection time.
        return dt.date().isoformat()
    except (TypeError, ValueError, OverflowError, OSError):
        return None


def in_launch_window(record, start=None, end=None):
    if not start and not end:
        return True
    value = launch_date(record)
    return bool(value and (not start or value >= start) and (not end or value <= end))
