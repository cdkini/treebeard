"""Compact relative-time strings for picker rows."""

from __future__ import annotations

_MINUTE = 60
_HOUR = 60 * _MINUTE
_DAY = 24 * _HOUR
_WEEK = 7 * _DAY


def humanize_mtime(seconds_ago: float) -> str:
    """Render a non-negative seconds delta as a short ago-string.

    Buckets: <60s -> "just now", <60m -> "Nm ago", <24h -> "Nh ago",
    <7d -> "Nd ago", else "Nw ago". Negative deltas (clock skew) clamp
    to "just now".
    """
    if seconds_ago < _MINUTE:
        return "just now"
    if seconds_ago < _HOUR:
        return f"{int(seconds_ago // _MINUTE)}m ago"
    if seconds_ago < _DAY:
        return f"{int(seconds_ago // _HOUR)}h ago"
    if seconds_ago < _WEEK:
        return f"{int(seconds_ago // _DAY)}d ago"
    return f"{int(seconds_ago // _WEEK)}w ago"
