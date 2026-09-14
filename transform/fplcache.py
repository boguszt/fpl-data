"""Read Randdalf/fplcache snapshots without copying them into raw/."""

from __future__ import annotations

import json
import lzma
import os
from datetime import date, datetime, timezone
from pathlib import Path

from ingest.client import season_from_bootstrap
from ingest.paths import REPO_ROOT

FPLCACHE_DEFAULT = REPO_ROOT.parent / "fplcache-src"


def fplcache_dir() -> Path | None:
    env = os.environ.get("FPLCACHE_DIR")
    root = Path(env) if env else FPLCACHE_DEFAULT
    cache = root / "cache" if (root / "cache").is_dir() else root
    if not cache.is_dir():
        return None
    return cache


def list_fplcache_files(cache: Path) -> list[Path]:
    return sorted(p for p in cache.glob("*/*/*/*.json.xz") if p.is_file())


def parse_fplcache_ts(path: Path) -> datetime:
    hhmm = path.name.split(".")[0]
    day = int(path.parent.name)
    month = int(path.parent.parent.name)
    year = int(path.parent.parent.parent.name)
    hour, minute = int(hhmm[:2]), int(hhmm[2:])
    return datetime(year, month, day, hour, minute, tzinfo=timezone.utc)


def inspect_fplcache(cache: Path, *, measure_uncompressed: bool = True) -> dict:
    files = list_fplcache_files(cache)
    if not files:
        raise FileNotFoundError(f"no cache/*/*/*/*.json.xz under {cache}")
    stamps = [parse_fplcache_ts(p) for p in files]
    days = sorted({ts.date() for ts in stamps})
    gaps: list[tuple[date, date, int]] = []
    for a, b in zip(days, days[1:]):
        delta = (b - a).days
        if delta > 2:
            gaps.append((a, b, delta))
    uncompressed = None
    if measure_uncompressed:
        uncompressed = 0
        for path in files:
            with lzma.open(path, "rb") as fh:
                while True:
                    chunk = fh.read(1024 * 1024)
                    if not chunk:
                        break
                    uncompressed += len(chunk)
    return {
        "n_files": len(files),
        "earliest": min(stamps),
        "latest": max(stamps),
        "uncompressed": uncompressed,
        "compressed": sum(p.stat().st_size for p in files),
        "n_days": len(days),
        "gaps": gaps,
        "files": files,
        "stamps": stamps,
    }


def _optional_int(v):
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None


def _optional_float(v):
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _optional_str(v):
    if v is None:
        return None
    text = str(v).strip()
    return text or None


def rows_from_payload(ts: datetime, payload: dict, source: str) -> list[dict]:
    try:
        season = season_from_bootstrap(payload)
    except (ValueError, KeyError, TypeError) as exc:
        raise ValueError(f"cannot derive season from events: {exc}") from exc
    iso = ts.isoformat()
    rows = []
    for el in payload.get("elements") or []:
        code = el.get("code")
        if code is None:
            continue
        rows.append(
            {
                "snapshot_ts": iso,
                "season": season,
                "player_code": int(code),
                "now_cost": _optional_int(el.get("now_cost")),
                "selected_by_percent": _optional_float(el.get("selected_by_percent")),
                "transfers_in_event": _optional_int(el.get("transfers_in_event")),
                "transfers_out_event": _optional_int(el.get("transfers_out_event")),
                "status": _optional_str(el.get("status")),
                "chance_of_playing_next_round": _optional_int(
                    el.get("chance_of_playing_next_round")
                ),
                "news": _optional_str(el.get("news")),
                "source": source,
            }
        )
    return rows


def load_fplcache_rows(cache: Path, files: list[Path] | None = None) -> tuple[list[dict], int]:
    """Stream .xz files; return rows and uncompressed byte count. Never writes decompressed JSON."""
    paths = files if files is not None else list_fplcache_files(cache)
    rows: list[dict] = []
    uncompressed = 0
    for i, path in enumerate(paths, start=1):
        ts = parse_fplcache_ts(path)
        with lzma.open(path, "rt", encoding="utf-8") as fh:
            text = fh.read()
        uncompressed += len(text.encode("utf-8"))
        try:
            payload = json.loads(text)
            rows.extend(rows_from_payload(ts, payload, "fplcache"))
        except (ValueError, json.JSONDecodeError) as exc:
            print(f"  skip {path}: {exc}", flush=True)
        if i % 500 == 0:
            print(f"  fplcache {i}/{len(paths)}", flush=True)
    return rows, uncompressed


def print_inspect(info: dict) -> None:
    print("fplcache inspect", flush=True)
    print(f"  files: {info['n_files']}", flush=True)
    print(f"  earliest: {info['earliest'].isoformat()}", flush=True)
    print(f"  latest:   {info['latest'].isoformat()}", flush=True)
    print(f"  distinct days: {info['n_days']}", flush=True)
    unc = (
        f"{info['uncompressed']:,} bytes"
        if info.get("uncompressed") is not None
        else "not measured"
    )
    print(
        f"  compressed: {info['compressed']:,} bytes  "
        f"uncompressed: {unc}",
        flush=True,
    )
    if not info["gaps"]:
        print("  gaps >2 days: none", flush=True)
        return
    print(f"  gaps >2 days: {len(info['gaps'])}", flush=True)
    for a, b, delta in info["gaps"]:
        print(f"    {a.isoformat()} -> {b.isoformat()}  ({delta} days)", flush=True)
