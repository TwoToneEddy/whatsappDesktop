from datetime import datetime
import math


MILLISECOND_THRESHOLD = 100_000_000_000


def timestamp_seconds(value):
    try:
        timestamp = float(value)
    except (TypeError, ValueError, OverflowError):
        return 0.0
    if not math.isfinite(timestamp) or timestamp < 0:
        return 0.0
    if timestamp >= MILLISECOND_THRESHOLD:
        timestamp /= 1000
    return timestamp if timestamp < MILLISECOND_THRESHOLD else 0.0


def format_timestamp(value, pattern):
    timestamp = timestamp_seconds(value)
    if not timestamp:
        return "Unknown time"
    try:
        return datetime.fromtimestamp(timestamp).strftime(pattern)
    except (ValueError, OverflowError, OSError):
        return "Unknown time"
