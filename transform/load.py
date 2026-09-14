from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from ingest.client import read_json
from ingest.paths import DATA, RAW, SEASONS, latest_vaastav_file


def _read_csv(path: Path) -> pd.DataFrame:
    try:
        return pd.read_csv(path, low_memory=False, encoding="utf-8")
    except UnicodeDecodeError:
        return pd.read_csv(path, low_memory=False, encoding="latin-1")


def _season_csv(season: str, filename: str) -> Path | None:
    return latest_vaastav_file(season, filename)


def load_players_raw() -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for season in SEASONS:
        path = _season_csv(season, "players_raw.csv")
        if path is None:
            continue
        df = _read_csv(path)
        df["season"] = season
        frames.append(df)
    if not frames:
        raise FileNotFoundError("no players_raw.csv under raw/vaastav; run ingest/backfill.py")
    return pd.concat(frames, ignore_index=True, sort=False)


def load_master_team_list() -> pd.DataFrame:
    path = RAW / "vaastav" / "master_team_list.csv"
    if not path.exists():
        raise FileNotFoundError(path)
    return _read_csv(path)


def load_merged_gw() -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for season in SEASONS:
        path = _season_csv(season, "merged_gw.csv")
        if path is None:
            continue
        df = _read_csv(path)
        df["season"] = season
        frames.append(df)
    if not frames:
        raise FileNotFoundError("no merged_gw.csv under raw/vaastav; run ingest/backfill.py")
    return pd.concat(frames, ignore_index=True, sort=False)


def load_teams_csv() -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for season in SEASONS:
        path = _season_csv(season, "teams.csv")
        if path is None:
            continue
        df = _read_csv(path)
        df["season"] = season
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True, sort=False)


def load_vaastav_fixtures() -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for season in SEASONS:
        path = _season_csv(season, "fixtures.csv")
        if path is None:
            continue
        df = _read_csv(path)
        df["season"] = season
        frames.append(df)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True, sort=False)


def parse_snapshot_ts(path: Path) -> datetime:
    day = path.parent.name
    hhmm = path.name.split(".")[0]
    return datetime.strptime(f"{day} {hhmm}", "%Y-%m-%d %H%M").replace(tzinfo=timezone.utc)


def list_bootstrap_paths() -> list[Path]:
    root = RAW / "bootstrap"
    if not root.exists():
        return []
    paths = list(root.glob("*/*.json.gz")) + list(root.glob("*/*.json"))
    return sorted(paths, key=parse_snapshot_ts)


def load_bootstrap_snapshots() -> list[tuple[datetime, dict]]:
    out: list[tuple[datetime, dict]] = []
    for path in list_bootstrap_paths():
        out.append((parse_snapshot_ts(path), read_json(path)))
    return out


def latest_bootstrap() -> tuple[datetime, dict]:
    snaps = load_bootstrap_snapshots()
    if not snaps:
        raise FileNotFoundError("no bootstrap snapshots under raw/bootstrap")
    return snaps[-1]


def load_live_payloads() -> list[tuple[str, int, dict]]:
    root = RAW / "live"
    if not root.exists():
        return []
    out: list[tuple[str, int, dict]] = []
    for path in sorted(root.glob("*/gw*.json")):
        season = path.parent.name
        match = re.fullmatch(r"gw(\d+)", path.stem)
        if not match:
            continue
        out.append((season, int(match.group(1)), read_json(path)))
    return out


def load_latest_api_fixtures() -> dict[str, list]:
    """Latest dated fixtures JSON per season. Supports season/date/HHMM.json or season/date.json."""
    root = RAW / "fixtures"
    if not root.exists():
        return {}
    ranked: dict[str, tuple[str, Path]] = {}
    for path in root.rglob("*.json"):
        if path.name.endswith(".tmp"):
            continue
        rel = path.relative_to(root)
        if not rel.parts:
            continue
        season = rel.parts[0]
        sort_key = "/".join(rel.parts[1:])
        prev = ranked.get(season)
        if prev is None or sort_key > prev[0]:
            ranked[season] = (sort_key, path)
    return {season: read_json(path) for season, (_, path) in ranked.items()}


def load_history_past() -> pd.DataFrame:
    root = RAW / "element-summary"
    if not root.exists():
        return pd.DataFrame()
    rows: list[dict] = []
    for path in root.glob("*.json"):
        file_code = int(path.stem)
        payload = read_json(path)
        for row in payload.get("history_past") or []:
            season_name = str(row.get("season_name") or "").replace("/", "-")
            rows.append(
                {
                    "player_code": row.get("element_code") or file_code,
                    "season": season_name,
                    "start_cost": row.get("start_cost"),
                    "end_cost": row.get("end_cost"),
                    "total_points": row.get("total_points"),
                    "minutes": row.get("minutes"),
                    "goals_scored": row.get("goals_scored"),
                    "assists": row.get("assists"),
                    "clean_sheets": row.get("clean_sheets"),
                    "goals_conceded": row.get("goals_conceded"),
                    "own_goals": row.get("own_goals"),
                    "penalties_saved": row.get("penalties_saved"),
                    "penalties_missed": row.get("penalties_missed"),
                    "yellow_cards": row.get("yellow_cards"),
                    "red_cards": row.get("red_cards"),
                    "saves": row.get("saves"),
                    "bonus": row.get("bonus"),
                    "bps": row.get("bps"),
                    "influence": row.get("influence"),
                    "creativity": row.get("creativity"),
                    "threat": row.get("threat"),
                    "ict_index": row.get("ict_index"),
                }
            )
    if not rows:
        return pd.DataFrame()
    return pd.DataFrame(rows)


def load_metric_direction() -> pd.DataFrame:
    path = DATA / "metric_direction.csv"
    df = pd.read_csv(path, dtype={"applies_to_positions": "string"})
    df["metric_name"] = df["metric_name"].astype("string")
    df["direction"] = df["direction"].astype("string")
    df["display_name"] = df["display_name"].astype("string")
    df["group"] = df["group"].astype("string")
    return df


def load_region_lookup() -> pd.DataFrame:
    path = DATA / "region_lookup.csv"
    df = pd.read_csv(path)
    df["region_id"] = pd.to_numeric(df["region_id"], errors="coerce").astype("Int64")
    df["country_name"] = df["country_name"].astype("string")
    df["iso_alpha2"] = df["iso_alpha2"].astype("string")
    df["iso_alpha3"] = df["iso_alpha3"].astype("string")
    return df


def load_style_features() -> pd.DataFrame:
    path = DATA / "style_features.csv"
    df = pd.read_csv(path, dtype={"first_season": "string"})
    df["name"] = df["name"].astype("string")
    df["numerator"] = df["numerator"].astype("string")
    df["denominator"] = df["denominator"].astype("string")
    df["category"] = df["category"].astype("string")
    df["in_clustering"] = pd.to_numeric(df["in_clustering"], errors="coerce").astype("Int64")
    return df
