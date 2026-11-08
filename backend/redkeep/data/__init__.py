"""Read-only clients for the public data sources (DESIGN.md section 11)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

EPOCH = datetime(2020, 1, 1, tzinfo=timezone.utc)


def chunks(start: datetime, end: datetime, size: timedelta) -> list[tuple[datetime, datetime]]:
    """Windows covering ``start`` to ``end`` on a fixed grid of ``size``, so a rerun asks for the same windows
    and finds the finished ones in the cache; the last one stops at ``end``."""
    t = EPOCH + ((start - EPOCH) // size) * size
    out = []
    while t < end:
        out.append((t, min(t + size, end)))
        t += size
    return out
