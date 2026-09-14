"""Build fact_player_season_opta from raw Pulse dumps."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from ingest.client import read_json
from ingest.paths import RAW, latest_plstats_dir, latest_plstats_player
from ingest.plstats import appearance_rows, opta_from_row, pulse_id_from_row

DERIVED = (
    "pass_accuracy",
    "cross_accuracy",
    "duel_win_pct",
    "aerial_win_pct",
    "shot_accuracy",
)


def _ratio(num, den) -> float | None:
    if num is None or den is None:
        return None
    try:
        d = float(den)
        n = float(num)
    except (TypeError, ValueError):
        return None
    if d == 0:
        return None
    return n / d


def _win_pct(won, lost) -> float | None:
    if won is None and lost is None:
        return None
    w = 0.0 if won is None else float(won)
    l = 0.0 if lost is None else float(lost)
    return _ratio(w, w + l)


def _list_seasons() -> list[str]:
    root = RAW / "plstats"
    if not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if p.is_dir())


def build_fact_player_season_opta() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (fact table, coverage table of first season per stat)."""
    rows: list[dict] = []
    for season in _list_seasons():
        folder = latest_plstats_dir(season)
        if folder is None:
            continue
        payload = read_json(folder / "appearances.json")
        for item in appearance_rows(payload):
            pid = pulse_id_from_row(item)
            opta = opta_from_row(item)
            if pid is None or opta is None:
                continue
            path = latest_plstats_player(season, pid)
            if path is None:
                continue
            stats_payload = read_json(path)
            rec: dict = {
                "season": season,
                "player_code": int(opta),
                "pulse_id": pid,
            }
            for stat in stats_payload.get("stats") or []:
                name = stat.get("name")
                if not name:
                    continue
                rec[str(name)] = stat.get("value")
            rec["pass_accuracy"] = _ratio(rec.get("accurate_pass"), rec.get("total_pass"))
            rec["cross_accuracy"] = _ratio(rec.get("accurate_cross"), rec.get("total_cross"))
            rec["duel_win_pct"] = _win_pct(rec.get("duel_won"), rec.get("duel_lost"))
            rec["aerial_win_pct"] = _win_pct(rec.get("aerial_won"), rec.get("aerial_lost"))
            rec["shot_accuracy"] = _ratio(
                rec.get("ontarget_scoring_att"), rec.get("total_scoring_att")
            )
            rows.append(rec)
    if not rows:
        return pd.DataFrame(columns=["season", "player_code", "pulse_id"]), pd.DataFrame(
            columns=["stat", "first_season", "seasons", "players"]
        )
    frame = pd.DataFrame(rows)
    coverage = _coverage_table(frame)
    return frame, coverage


def _coverage_table(frame: pd.DataFrame) -> pd.DataFrame:
    skip = {"season", "player_code", "pulse_id"}
    recs = []
    for col in frame.columns:
        if col in skip:
            continue
        present = frame.loc[frame[col].notna(), "season"]
        if present.empty:
            recs.append(
                {"stat": col, "first_season": None, "seasons": 0, "players": 0}
            )
            continue
        recs.append(
            {
                "stat": col,
                "first_season": str(present.min()),
                "seasons": int(present.nunique()),
                "players": int(frame[col].notna().sum()),
            }
        )
    out = pd.DataFrame(recs).sort_values(["first_season", "stat"], na_position="last")
    return out.reset_index(drop=True)


def print_coverage(coverage: pd.DataFrame) -> None:
    if coverage.empty:
        print("fact_player_season_opta coverage: (empty)", flush=True)
        return
    print("\n== Opta stat first-seen season (coverage boundary) ==", flush=True)
    print(coverage.to_string(index=False), flush=True)
