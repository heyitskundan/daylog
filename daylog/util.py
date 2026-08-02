"""Small shared helpers (time formatting, etc.)."""

from __future__ import annotations

from datetime import datetime, timezone


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def iso(dt: datetime) -> str:
    """ISO-8601 UTC string, e.g. 2026-06-23T14:05:00+00:00."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def parse_iso(value: str) -> datetime:
    """Parse an ISO-8601 string (handles a trailing 'Z')."""
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def human_duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds}s"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes}m" if secs == 0 else f"{minutes}m {secs}s"
    hours, minutes = divmod(minutes, 60)
    return f"{hours}h {minutes}m"


def local_date_str(dt: datetime | None = None) -> str:
    """YYYY-MM-DD in local time (used for daily notes / grouping)."""
    dt = dt or datetime.now()
    if dt.tzinfo is not None:
        dt = dt.astimezone()
    return dt.strftime("%Y-%m-%d")
