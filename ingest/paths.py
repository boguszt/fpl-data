from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RAW = REPO_ROOT / "raw"
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


def latest_fixtures_file(season: str) -> Path | None:
    root = RAW / "fixtures" / season
    if not root.exists():
        return None
    files = [p for p in root.rglob("*.json") if not p.name.endswith(".tmp")]
    if not files:
        return None
    return max(files, key=lambda p: str(p.relative_to(root)).replace("\\", "/"))

