"""Immutable record of when each feed was last successfully fetched."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ingest.client import stamp_parts, write_json_immutable
from ingest.paths import RAW

FEED_FETCH_ROOT = RAW / "feed_fetch"


def utc_iso(now: datetime | None = None) -> str:
    now = now or datetime.now(timezone.utc)
    return now.strftime("%Y-%m-%dT%H:%M:%SZ")


def parse_iso(value: str) -> datetime:
    text = value.strip()
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    return datetime.fromisoformat(text)


def write_feed_fetch(feeds: dict[str, dict[str, str]], *, fetched_at: str | None = None) -> Path:
    """Write one snapshot of per-feed as_of times. `feeds` values need `as_of`."""
    day, hhmm = stamp_parts()
    path = FEED_FETCH_ROOT / day / f"{hhmm}.json"
    payload = {"fetched_at": fetched_at or utc_iso(), "feeds": feeds}
    if not write_json_immutable(path, payload):
        path = FEED_FETCH_ROOT / day / f"{hhmm}a.json"
        write_json_immutable(path, payload)
    return path


def latest_feed_fetch() -> dict[str, Any] | None:
    if not FEED_FETCH_ROOT.exists():
        return None
    files = [
        p
        for p in FEED_FETCH_ROOT.rglob("*.json")
        if not p.name.endswith(".tmp")
    ]
    if not files:
        return None
    path = max(files, key=lambda p: str(p.relative_to(FEED_FETCH_ROOT)).replace("\\", "/"))
    return json.loads(path.read_text(encoding="utf-8"))
