"""web/data/status.json — per-feed fetch times, rewritten every export."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ingest.client import finalised_gameweeks, season_from_bootstrap
from ingest.feed_fetch import latest_feed_fetch, parse_iso, utc_iso
from ingest.paths import RAW, REPO_ROOT, latest_plstats_dir, latest_vaastav_file
from transform.load import latest_bootstrap, parse_snapshot_ts

WEB_DATA = REPO_ROOT / "web" / "data"
STALE_HOURS = 30
SCHEDULED_FEEDS = ("fpl", "opta", "vaastav")
FEED_NOTES = {
    "fpl": "bootstrap snapshot, fixtures",
    "opta": "season totals",
    "style": "fitted model, rebuilt on demand",
}


class FeedStallError(RuntimeError):
    pass


def _require_fresh() -> bool:
    if os.environ.get("FPL_REQUIRE_FRESH_FEEDS") == "1":
        return True
    return os.environ.get("CI", "").lower() == "true"


def _load_json(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _iso_from_dated_rel(path: Path, root: Path) -> str | None:
    try:
        parts = path.relative_to(root).parts
    except ValueError:
        return None
    day = None
    hhmm = "0000"
    for part in parts:
        if len(part) == 10 and part[4] == "-" and part[7] == "-":
            day = part
        elif len(part) == 4 and part.isdigit():
            hhmm = part
    if day is None:
        return None
    return datetime.strptime(f"{day} {hhmm}", "%Y-%m-%d %H%M").replace(
        tzinfo=timezone.utc
    ).strftime("%Y-%m-%dT%H:%M:%SZ")


def _latest_event_status() -> dict | None:
    root = RAW / "event-status"
    if not root.exists():
        return None
    files = [
        p
        for p in root.glob("*/*.json")
        if not p.name.endswith(".tmp")
    ]
    if not files:
        return None
    path = max(files, key=parse_snapshot_ts)
    return json.loads(path.read_text(encoding="utf-8"))


def _gw_fields(bootstrap: dict, event_status: dict | None) -> tuple[int, int]:
    done = finalised_gameweeks(bootstrap, event_status)
    latest_finalised = max(done) if done else 0
    events = sorted(bootstrap.get("events") or [], key=lambda e: int(e["id"]))
    in_progress = latest_finalised
    for ev in events:
        gid = int(ev["id"])
        if gid > latest_finalised:
            in_progress = gid
            break
    return latest_finalised, in_progress


def _as_of_from(
    name: str,
    stamps: dict[str, Any],
    prev: dict[str, Any],
    fallback: str | None,
) -> str | None:
    for src in (
        ((stamps.get("feeds") or {}).get(name) or {}).get("as_of"),
        ((prev.get("feeds") or {}).get(name) or {}).get("as_of"),
        fallback,
    ):
        if src:
            return str(src)
    return None


def _fallback_fpl() -> str | None:
    try:
        ts, _ = latest_bootstrap()
    except (FileNotFoundError, ValueError):
        return None
    return utc_iso(ts)


def _fallback_vaastav(season: str) -> str | None:
    path = latest_vaastav_file(season, "merged_gw.csv")
    if path is None:
        return None
    return _iso_from_dated_rel(path, RAW / "vaastav" / season)


def _fallback_opta(season: str) -> str | None:
    folder = latest_plstats_dir(season)
    if folder is None:
        return None
    return _iso_from_dated_rel(folder, RAW / "plstats" / season)


def _feed_obj(as_of: str | None, name: str) -> dict[str, str]:
    if not as_of:
        return {}
    out = {"as_of": as_of}
    note = FEED_NOTES.get(name)
    if note:
        out["note"] = note
    return out


def build_status(*, style_catalog_changed: bool, generated_at: str | None = None) -> dict[str, Any]:
    generated_at = generated_at or utc_iso()
    _, bootstrap = latest_bootstrap()
    current_season = season_from_bootstrap(bootstrap)
    latest_finalised, gw_in_progress = _gw_fields(bootstrap, _latest_event_status())
    stamps = latest_feed_fetch() or {}
    prev = _load_json(WEB_DATA / "status.json")

    fpl_as_of = _as_of_from("fpl", stamps, prev, _fallback_fpl())
    opta_as_of = _as_of_from("opta", stamps, prev, _fallback_opta(current_season))
    vaastav_as_of = _as_of_from("vaastav", stamps, prev, _fallback_vaastav(current_season))

    prev_style = ((prev.get("feeds") or {}).get("style") or {}).get("as_of")
    if style_catalog_changed or not prev_style:
        style_as_of = generated_at
    else:
        style_as_of = str(prev_style)

    feeds = {
        "fpl": _feed_obj(fpl_as_of, "fpl"),
        "opta": _feed_obj(opta_as_of, "opta"),
        "vaastav": _feed_obj(vaastav_as_of, "vaastav"),
        "style": _feed_obj(style_as_of, "style"),
    }
    return {
        "generated_at": generated_at,
        "feeds": feeds,
        "current_season": current_season,
        "latest_finalised_gw": latest_finalised,
        "gw_in_progress": gw_in_progress,
    }


def assert_scheduled_feeds_fresh(status: dict[str, Any], *, now: datetime | None = None) -> None:
    now = now or datetime.now(timezone.utc)
    errors: list[str] = []
    for name in SCHEDULED_FEEDS:
        feed = (status.get("feeds") or {}).get(name) or {}
        as_of = feed.get("as_of")
        if not as_of:
            errors.append(f"{name}: missing as_of (scheduled feed never stamped)")
            continue
        age_h = (now - parse_iso(str(as_of))).total_seconds() / 3600.0
        if age_h > STALE_HOURS:
            errors.append(
                f"{name}: as_of {as_of} is {age_h:.1f}h old "
                f"(limit {STALE_HOURS}h) - silent stall"
            )
    if errors:
        raise FeedStallError(
            "feed freshness check failed:\n- " + "\n- ".join(errors)
        )


def write_web_status(*, style_catalog_changed: bool) -> dict[str, Any]:
    payload = build_status(style_catalog_changed=style_catalog_changed)
    path = WEB_DATA / "status.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    path.write_bytes(encoded)
    print(f"  {'status.json':28} {len(encoded):8,} bytes  rewritten", flush=True)
    if _require_fresh():
        assert_scheduled_feeds_fresh(payload)
        print(f"  feed freshness ok (scheduled feeds < {STALE_HOURS}h)", flush=True)
    return payload
