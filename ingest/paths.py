from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RAW = REPO_ROOT / "raw"
DATA = REPO_ROOT / "data"
MARTS = REPO_ROOT / "marts"
DB_DIR = REPO_ROOT / "db"
DB_PATH = DB_DIR / "fpl.duckdb"

# 2016-17 through 2026-27 inclusive.
SEASONS = [f"{year}-{str(year + 1)[2:]}" for year in range(2016, 2027)]

VAASTAV_BASE = (
    "https://raw.githubusercontent.com/vaastav/Fantasy-Premier-League/master"
)
VAASTAV_API = (
    "https://api.github.com/repos/vaastav/Fantasy-Premier-League/contents"
)
FPL_BASE = "https://fantasy.premierleague.com/api"

# Remote path relative to data/{season}/ -> local filename under raw/vaastav/{season}/
VAASTAV_SEASON_FILES = (
    ("gws/merged_gw.csv", "merged_gw.csv"),
    ("players_raw.csv", "players_raw.csv"),
    ("fixtures.csv", "fixtures.csv"),
    ("teams.csv", "teams.csv"),
)

USER_AGENT = (
    "Mozilla/5.0 (compatible; fpl-data/0.1; personal Premier League analytics)"
)


def latest_vaastav_file(season: str, filename: str) -> Path | None:
    """Latest copy of a vaastav CSV for a season. Dated current-season files win."""
    root = RAW / "vaastav" / season
    if not root.exists():
        return None
    dated: list[Path] = []
    for path in root.rglob(filename):
        if "understat" in path.parts:
            continue
        rel = path.relative_to(root)
        if rel.name != filename:
            continue
        if rel.parts[0] == filename and len(rel.parts) == 1:
            continue
        dated.append(path)
    if dated:
        return max(dated, key=lambda p: str(p).replace("\\", "/"))
    flat = root / filename
    return flat if flat.exists() else None


def latest_plstats_dir(season: str) -> Path | None:
    """Latest Opta dump for a season: dated current-season folder, else the flat archive."""
    root = RAW / "plstats" / season
    if not root.is_dir():
        return None
    dated = [
        p
        for p in root.iterdir()
        if p.is_dir() and len(p.name) == 10 and p.name[4] == "-" and p.name[7] == "-"
        and (p / "appearances.json").exists()
    ]
    if dated:
        return max(dated, key=lambda p: p.name)
    if (root / "appearances.json").exists():
        return root
    return None


def latest_plstats_player(season: str, pulse_id: int) -> Path | None:
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
        reverse=True,
    )
    for path in dated:
        if path.exists():
            return path
    flat = root / "players" / f"{pulse_id}.json"
    return flat if flat.exists() else None


def latest_fixtures_file(season: str) -> Path | None:
    root = RAW / "fixtures" / season
    if not root.exists():
        return None
    files = [p for p in root.rglob("*.json") if not p.name.endswith(".tmp")]
    if not files:
        return None
    return max(files, key=lambda p: str(p.relative_to(root)).replace("\\", "/"))

