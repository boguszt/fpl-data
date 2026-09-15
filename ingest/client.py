from __future__ import annotations

import gzip
import hashlib
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote, urlsplit, urlunsplit
from urllib.request import Request, urlopen

import httpx

from ingest.paths import (
    FPL_BASE,
    RAW,
    SEASONS,
    USER_AGENT,
    VAASTAV_API,
    VAASTAV_BASE,
    VAASTAV_SEASON_FILES,
    latest_fixtures_file,
    latest_vaastav_file,
)


def _fetch_raw(url: str, timeout: int = 60) -> bytes:
    parts = urlsplit(url)
    encoded = urlunsplit(
        (parts.scheme, parts.netloc, quote(parts.path, safe="/"), parts.query, parts.fragment)
    )
    req = Request(encoded, headers={"User-Agent": USER_AGENT})
    with urlopen(req, timeout=timeout) as resp:
        return resp.read()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def stamp_parts(now: datetime | None = None) -> tuple[str, str]:
    now = now or _utc_now()
    return now.strftime("%Y-%m-%d"), now.strftime("%H%M")


def write_bytes_immutable(path: Path, data: bytes) -> bool:
    """Write `data` only if `path` does not already exist. Returns True if written."""
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)
    return True


def write_json_immutable(path: Path, payload: Any) -> bool:
    blob = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    return write_bytes_immutable(path, blob)


def write_json_gz_immutable(path: Path, payload: Any) -> bool:
    if path.exists():
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    with gzip.open(tmp, "wt", encoding="utf-8") as fh:
        json.dump(payload, fh, separators=(",", ":"))
    tmp.replace(path)
    return True


def read_json(path: Path) -> Any:
    if path.suffix == ".gz" or path.name.endswith(".json.gz"):
        with gzip.open(path, "rt", encoding="utf-8") as fh:
            return json.load(fh)
    return json.loads(path.read_text(encoding="utf-8"))


def season_from_bootstrap(bootstrap: dict) -> str:
    events = bootstrap.get("events") or []
    if not events:
        raise ValueError("bootstrap-static has no events")
    first = min(events, key=lambda e: e["id"])
    raw = first["deadline_time"].replace("Z", "+00:00")
    deadline = datetime.fromisoformat(raw)
    year = deadline.year if deadline.month >= 8 else deadline.year - 1
    return f"{year}-{str(year + 1)[2:]}"


def finalised_gameweeks(bootstrap: dict, event_status: dict | None) -> list[int]:
    bonus_events: set[int] = set()
    if event_status:
        for row in event_status.get("status") or []:
            if row.get("bonus_added") and row.get("event") is not None:
                bonus_events.add(int(row["event"]))
    out: set[int] = set()
    for ev in bootstrap.get("events") or []:
        gw = int(ev["id"])
        if (ev.get("finished") and ev.get("data_checked")) or gw in bonus_events:
            out.add(gw)
    return sorted(out)


class FplClient:
    def __init__(self, timeout: float = 60.0) -> None:
        self._http = httpx.Client(
            timeout=timeout,
            headers={"User-Agent": USER_AGENT, "Accept": "*/*"},
            follow_redirects=True,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> FplClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def get_json(self, url: str) -> Any:
        return json.loads(self._get(url))

    def get_bytes_or_none(self, url: str) -> bytes | None:
        try:
            return self._get(url)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise

    def _get(self, url: str, attempts: int = 5) -> bytes:
        delay = 1.0
        last_exc: Exception | None = None
        for _ in range(attempts):
            try:
                resp = self._http.get(url)
                if resp.status_code in {429, 500, 502, 503, 504}:
                    time.sleep(delay)
                    delay = min(delay * 2, 30)
                    last_exc = httpx.HTTPStatusError(
                        f"{resp.status_code} for {url}",
                        request=resp.request,
                        response=resp,
                    )
                    continue
                resp.raise_for_status()
                return resp.content
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_exc = exc
                time.sleep(delay)
                delay = min(delay * 2, 30)
        if last_exc:
            raise last_exc
        raise RuntimeError(f"GET failed: {url}")

    def download_vaastav(self, current_season: str | None = None) -> bool:
        """Return True if the live season was fetched (HTTP 200), including unchanged bytes."""
        master = f"{VAASTAV_BASE}/data/master_team_list.csv"
        dest = RAW / "vaastav" / "master_team_list.csv"
        if not dest.exists():
            blob = self._get(master)
            write_bytes_immutable(dest, blob)
            print(f"wrote {dest}")
        else:
            print(f"skip existing {dest}")

        fetched_live = False
        for season in SEASONS:
            if current_season and season == current_season:
                fetched_live = self._refresh_live_season_vaastav(season)
                continue
            for remote_rel, local_name in VAASTAV_SEASON_FILES:
                dest = RAW / "vaastav" / season / local_name
                if dest.exists():
                    print(f"skip existing {dest}")
                    continue
                url = f"{VAASTAV_BASE}/data/{season}/{remote_rel}"
                blob = self.get_bytes_or_none(url)
                if blob is None:
                    print(f"404 skip {url}")
                    continue
                write_bytes_immutable(dest, blob)
                print(f"wrote {dest} ({len(blob)} bytes)")
        return fetched_live

    def _refresh_live_season_vaastav(self, season: str) -> bool:
        """Re-fetch mutating current-season CSVs. Dated, immutable; skip if bytes unchanged."""
        day, hhmm = stamp_parts()
        fetched = False
        for remote_rel, local_name in VAASTAV_SEASON_FILES:
            url = f"{VAASTAV_BASE}/data/{season}/{remote_rel}"
            blob = self.get_bytes_or_none(url)
            if blob is None:
                print(f"404 skip {url}")
                continue
            fetched = True
            latest = latest_vaastav_file(season, local_name)
            if latest is not None and hashlib.sha256(latest.read_bytes()).digest() == hashlib.sha256(blob).digest():
                print(f"unchanged {season}/{local_name} (latest {latest})")
                continue
            dest = RAW / "vaastav" / season / day / local_name
            if dest.exists():
                dest = RAW / "vaastav" / season / day / hhmm / local_name
            write_bytes_immutable(dest, blob)
            print(f"wrote {dest} ({len(blob)} bytes)")
        return fetched

    def download_understat(self) -> None:
        """Dump vaastav understat/ trees plus season-level id_dict.csv. No transform."""
        print("=== vaastav understat (raw only) ===")
        for season in SEASONS:
            id_url = f"{VAASTAV_BASE}/data/{season}/id_dict.csv"
            id_dest = RAW / "vaastav" / "understat" / season / "id_dict.csv"
            if not id_dest.exists():
                blob = self.get_bytes_or_none(id_url)
                if blob is not None:
                    write_bytes_immutable(id_dest, blob)
                    print(f"wrote {id_dest}")
            listing = self._github_list(f"data/{season}/understat")
            if listing is None:
                print(f"no understat/ for {season}")
                continue
            files = [
                item
                for item in listing
                if item.get("type") == "file" and item.get("download_url")
            ]
            print(f"{season}/understat {len(files)} files")
            todo: list[tuple[str, Path]] = []
            for item in files:
                dest = RAW / "vaastav" / "understat" / season / item["name"]
                if dest.exists():
                    continue
                todo.append((item["download_url"], dest))
            if not todo:
                print("  all cached")
                continue
            written = 0
            with ThreadPoolExecutor(max_workers=8) as pool:
                futs = {pool.submit(_fetch_raw, url): dest for url, dest in todo}
                for fut in as_completed(futs):
                    dest = futs[fut]
                    try:
                        blob = fut.result()
                    except Exception as exc:  # noqa: BLE001
                        print(f"  FAIL {dest.name}: {exc}")
                        continue
                    if write_bytes_immutable(dest, blob):
                        written += 1
            print(f"  wrote {written} new files")

    def _github_list(self, path: str) -> list[dict] | None:
        url = f"{VAASTAV_API}/{path}"
        try:
            blob = self._get(url)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 404:
                return None
            raise
        payload = json.loads(blob)
        if not isinstance(payload, list):
            return None
        return payload

    def snapshot_bootstrap(self) -> dict:
        payload = self.get_json(f"{FPL_BASE}/bootstrap-static/")
        day, hhmm = stamp_parts()
        path = RAW / "bootstrap" / day / f"{hhmm}.json.gz"
        if write_json_gz_immutable(path, payload):
            print(f"wrote {path}")
        else:
            print(f"skip existing {path}")
        return payload

    def snapshot_event_status(self) -> dict:
        payload = self.get_json(f"{FPL_BASE}/event-status/")
        day, hhmm = stamp_parts()
        path = RAW / "event-status" / day / f"{hhmm}.json"
        if write_json_immutable(path, payload):
            print(f"wrote {path}")
        else:
            print(f"skip existing {path}")
        return payload

    def snapshot_fixtures(self, bootstrap: dict) -> list:
        season = season_from_bootstrap(bootstrap)
        payload = self.get_json(f"{FPL_BASE}/fixtures/")
        blob = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        latest = latest_fixtures_file(season)
        if latest is not None and hashlib.sha256(latest.read_bytes()).digest() == hashlib.sha256(blob).digest():
            print(f"unchanged fixtures {season} (latest {latest})")
            return payload
        day, hhmm = stamp_parts()
        path = RAW / "fixtures" / season / day / f"{hhmm}.json"
        if write_json_immutable(path, payload):
            print(f"wrote {path}")
        else:
            print(f"skip existing {path}")
        return payload

    def pull_live_if_missing(self, season: str, gw: int) -> bool:
        path = RAW / "live" / season / f"gw{gw}.json"
        if path.exists():
            print(f"skip existing {path}")
            return False
        payload = self.get_json(f"{FPL_BASE}/event/{gw}/live/")
        write_json_immutable(path, payload)
        n = len(payload.get("elements") or [])
        print(f"wrote {path} ({n} elements)")
        return True

    def cache_history_past(self, bootstrap: dict, pause_s: float = 1.0) -> None:
        elements = bootstrap.get("elements") or []
        total = len(elements)
        failures = 0
        print(f"element-summary history_past for {total} current players (~1 req/sec)")
        for i, el in enumerate(elements, start=1):
            code = el["code"]
            dest = RAW / "element-summary" / f"{code}.json"
            if dest.exists():
                if i % 100 == 0 or i == total:
                    print(f"  cache hit {i}/{total}")
                continue
            url = f"{FPL_BASE}/element-summary/{el['id']}/"
            try:
                payload = self.get_json(url)
                write_json_immutable(dest, payload)
            except Exception as exc:  # noqa: BLE001
                failures += 1
                print(f"  FAIL {url}: {exc}")
            if i % 25 == 0 or i == total:
                print(f"  fetched {i}/{total} (failures={failures})")
            time.sleep(pause_s)
        print(f"history_past done; failures={failures}")
