"""Premier League Pulse/Opta season-stat archive.

Ranked `/stats/ranked/players/{stat}` returns one metric per request and omits
zeros, so a complete dump is cheaper as one `/stats/player/{id}` per player
(~0.05-0.12s, every field) plus one ranked-appearances census per season.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from pathlib import Path
from typing import Any

import httpx

from ingest.client import season_from_bootstrap, stamp_parts, write_bytes_immutable
from ingest.paths import RAW, latest_plstats_dir

PULSE_BASE = "https://footballapi.pulselive.com/football"
ORIGIN = "https://www.premierleague.com"
MIN_CURRENT_ROSTER = 300
APPEARANCES_PAGE_SIZE = 1000
PLAYER_GAP_S = 0.08
COMPS = 1
FPL_FROM = "2016-17"


class PulseError(RuntimeError):
    pass


def _season_from_label(label: str) -> str | None:
    text = (label or "").strip()
    m = re.search(r"(19|20)(\d{2})\s*/\s*((?:19|20)?\d{2})", text)
    if not m:
        return None
    start = int(m.group(1) + m.group(2))
    end_raw = m.group(3)
    end = int(end_raw) if len(end_raw) == 4 else 2000 + int(end_raw)
    if end < 100:
        end += 2000
    return f"{start}-{str(end)[2:]}"


class PulseClient:
    def __init__(self, timeout: float = 60.0) -> None:
        self._http = httpx.Client(
            timeout=timeout,
            headers={
                "Origin": ORIGIN,
                "User-Agent": "Mozilla/5.0 (compatible; fpl-data/0.1; personal Premier League analytics)",
                "Accept": "*/*",
            },
            follow_redirects=True,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> PulseClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def get_bytes(self, path: str, params: dict[str, Any] | None = None) -> bytes:
        url = f"{PULSE_BASE}/{path.lstrip('/')}"
        delay = 1.0
        last_exc: Exception | None = None
        for _ in range(8):
            try:
                resp = self._http.get(url, params=params)
                if resp.status_code == 429:
                    wait = resp.headers.get("Retry-After")
                    sleep_s = float(wait) if wait and str(wait).isdigit() else delay
                    print(f"  429 backoff {sleep_s:.0f}s {path}", flush=True)
                    time.sleep(sleep_s)
                    delay = min(delay * 2, 120)
                    last_exc = PulseError(f"429 for {resp.url}")
                    continue
                if resp.status_code in {500, 502, 503, 504}:
                    time.sleep(delay)
                    delay = min(delay * 2, 60)
                    last_exc = PulseError(f"{resp.status_code} for {resp.url}")
                    continue
                if resp.status_code != 200:
                    raise PulseError(
                        f"Pulse {resp.status_code} for {resp.url}: "
                        f"{resp.text[:300]}"
                    )
                return resp.content
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_exc = exc
                time.sleep(delay)
                delay = min(delay * 2, 60)
        raise PulseError(f"GET failed after retries: {url} ({last_exc})")

    def get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        return json.loads(self.get_bytes(path, params))


def list_compseasons(client: PulseClient) -> list[dict]:
    payload = client.get_json(
        "competitions/1/compseasons", {"page": 0, "pageSize": 100}
    )
    rows = []
    for item in payload.get("content") or []:
        label = str(item.get("label") or "")
        season = _season_from_label(label)
        if season is None or item.get("id") is None:
            continue
        rows.append(
            {
                "season": season,
                "comp_season_id": int(item["id"]),
                "label": label,
            }
        )
    rows.sort(key=lambda r: r["season"])
    return rows


def resolve_comp_season_id(client: PulseClient, season: str) -> int:
    rows = list_compseasons(client)
    for row in rows:
        if row["season"] == season:
            return row["comp_season_id"]
    raise PulseError(
        f"no Pulse compSeason id for {season}; "
        f"available={', '.join(r['season'] for r in rows)}"
    )


def fetch_appearances_bytes(client: PulseClient, comp_season_id: int) -> bytes:
    """One ranked-appearances census. Verbatim if a single page; else merged content."""
    params = {
        "page": 0,
        "pageSize": APPEARANCES_PAGE_SIZE,
        "compSeasons": comp_season_id,
        "comps": COMPS,
        "altIds": "true",
    }
    first = client.get_bytes("stats/ranked/players/appearances", params)
    payload = json.loads(first)
    stats = payload.get("stats") or {}
    info = stats.get("pageInfo") or {}
    pages = int(info.get("numPages") or 1)
    if pages <= 1:
        return first
    content = list(stats.get("content") or [])
    for page in range(1, pages):
        params = dict(params)
        params["page"] = page
        more = json.loads(
            client.get_bytes("stats/ranked/players/appearances", params)
        )
        content.extend((more.get("stats") or {}).get("content") or [])
        time.sleep(PLAYER_GAP_S)
    merged = {
        "entity": payload.get("entity"),
        "stats": {
            "pageInfo": {
                "page": 0,
                "numPages": 1,
                "pageSize": len(content),
                "numEntries": len(content),
            },
            "content": content,
        },
    }
    return json.dumps(merged, separators=(",", ":")).encode("utf-8")


def appearance_rows(payload: dict) -> list[dict]:
    stats = payload.get("stats") or payload
    content = stats.get("content") if isinstance(stats, dict) else None
    if content is None and isinstance(payload.get("content"), list):
        content = payload["content"]
    return list(content or [])


def pulse_id_from_row(row: dict) -> int | None:
    owner = row.get("owner") or row
    raw = owner.get("id")
    if raw is None:
        return None
    return int(raw)


def opta_from_row(row: dict) -> str | None:
    owner = row.get("owner") or row
    alt = (owner.get("altIds") or {}).get("opta")
    if not alt:
        return None
    text = str(alt)
    return text[1:] if text.lower().startswith("p") else text


def _latest_player_file(season: str, pulse_id: int) -> Path | None:
    root = RAW / "plstats" / season
    if not root.is_dir():
        return None
    dated = sorted(
        (
            p / "players" / f"{pulse_id}.json"
            for p in root.iterdir()
            if p.is_dir() and len(p.name) == 10 and p.name[4] == "-"
        ),
        key=lambda p: p.parent.parent.name,
    )
    for path in reversed(dated):
        if path.exists():
            return path
    flat = root / "players" / f"{pulse_id}.json"
    return flat if flat.exists() else None


def _latest_appearances_file(season: str) -> Path | None:
    folder = latest_plstats_dir(season)
    if folder is None:
        return None
    path = folder / "appearances.json"
    return path if path.exists() else None


def _write_if_changed(path: Path, blob: bytes, previous: Path | None) -> str:
    if path.exists():
        return "skip"
    if previous is not None and previous.exists():
        if hashlib.sha256(previous.read_bytes()).digest() == hashlib.sha256(blob).digest():
            return "unchanged"
    if write_bytes_immutable(path, blob):
        return "wrote"
    return "skip"


def _assert_roster(rows: list[dict], season: str, *, current: bool) -> None:
    n = len(rows)
    print(f"  {season} ranked appearances: {n} players", flush=True)
    if current and n < MIN_CURRENT_ROSTER:
        raise PulseError(
            f"{season} Pulse roster has {n} players (need >={MIN_CURRENT_ROSTER}). "
            "Refusing to write a silently empty/short pull."
        )


def pull_season(
    client: PulseClient,
    season: str,
    comp_season_id: int,
    *,
    dated: bool,
) -> dict[str, int]:
    counts = {"wrote": 0, "skip": 0, "unchanged": 0}
    if dated:
        day, _ = stamp_parts()
        dest_root = RAW / "plstats" / season / day
    else:
        dest_root = RAW / "plstats" / season

    app_path = dest_root / "appearances.json"
    prev_app = _latest_appearances_file(season) if dated else None

    if app_path.exists():
        blob = app_path.read_bytes()
        counts["skip"] += 1
        print(f"  skip existing {app_path}", flush=True)
    else:
        blob = fetch_appearances_bytes(client, comp_season_id)
        flag = _write_if_changed(app_path, blob, prev_app)
        counts[flag] += 1
        print(f"  appearances {flag} ({len(blob)} bytes)", flush=True)
        if flag == "unchanged":
            blob = prev_app.read_bytes() if prev_app is not None else blob

    rows = appearance_rows(json.loads(blob))
    _assert_roster(rows, season, current=dated)

    players_dir = dest_root / "players"
    n = len(rows)
    for i, row in enumerate(rows, start=1):
        pid = pulse_id_from_row(row)
        if pid is None:
            raise PulseError(f"{season}: appearance row missing player id")
        path = players_dir / f"{pid}.json"
        prev = _latest_player_file(season, pid)
        if path.exists():
            counts["skip"] += 1
        else:
            player_blob = client.get_bytes(
                f"stats/player/{pid}",
                {"comps": COMPS, "compSeasons": comp_season_id},
            )
            flag = _write_if_changed(path, player_blob, prev)
            counts[flag] += 1
            time.sleep(PLAYER_GAP_S)
        if i % 50 == 0 or i == n:
            print(
                f"  {season} players {i}/{n}  wrote={counts['wrote']} "
                f"skip={counts['skip']} unchanged={counts['unchanged']}",
                flush=True,
            )
    return counts


def fpl_current_season() -> str | None:
    from transform.load import latest_bootstrap

    try:
        _, bootstrap = latest_bootstrap()
    except (FileNotFoundError, ValueError):
        return None
    return season_from_bootstrap(bootstrap)


def refresh_current_season(season: str | None = None) -> None:
    """Daily current-season refresh. Dated, skip-if-present, commit-on-byte-change."""
    with PulseClient() as client:
        if season is None:
            season = fpl_current_season()
        if not season:
            season = list_compseasons(client)[-1]["season"]
        comp_id = resolve_comp_season_id(client, season)
        print(
            f"=== Pulse Opta current season {season} (compSeasons={comp_id}) ===",
            flush=True,
        )
        pull_season(client, season, comp_id, dated=True)


def archive_seasons(
    *,
    from_season: str = FPL_FROM,
    until_season: str | None = None,
    current: str | None = None,
) -> None:
    with PulseClient() as client:
        rows = list_compseasons(client)
        if current is None:
            current = fpl_current_season() or (rows[-1]["season"] if rows else None)
        picked = [
            r
            for r in rows
            if r["season"] >= from_season
            and (until_season is None or r["season"] <= until_season)
        ]
        n_players_est = 540 * len(picked)
        print(
            f"Pulse archive {len(picked)} seasons  {from_season}..{until_season or picked[-1]['season']}  "
            f"~{n_players_est} player requests + {len(picked)} appearances  "
            f"(~{n_players_est * (PLAYER_GAP_S + 0.07) / 60:.0f} min at polite rate)",
            flush=True,
        )
        for row in picked:
            dated = bool(current and row["season"] == current)
            print(
                f"=== {row['season']} id={row['comp_season_id']} "
                f"{'dated-current' if dated else 'closed'} ===",
                flush=True,
            )
            pull_season(client, row["season"], row["comp_season_id"], dated=dated)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Archive Pulse Opta season stats")
    parser.add_argument("--from", dest="from_season", default=FPL_FROM)
    parser.add_argument("--until", dest="until_season", default=None)
    parser.add_argument(
        "--current",
        action="store_true",
        help="refresh only the current FPL season (dated)",
    )
    args = parser.parse_args(argv)
    if args.current:
        refresh_current_season()
        return
    archive_seasons(from_season=args.from_season, until_season=args.until_season)


if __name__ == "__main__":
    main()
