"""Season team table and fixture list for web/data."""

from __future__ import annotations

import math
from datetime import date, datetime, timezone

import pandas as pd

from ingest.client import season_from_bootstrap
from transform.derived import parse_scoreline
from transform.load import latest_bootstrap, load_teams_csv

TEAMS_FROM_SEASON = "2020-21"
XG_FROM_SEASON = "2022-23"
STRENGTH_KEYS = (
    "strength_overall_home",
    "strength_overall_away",
    "strength_attack_home",
    "strength_attack_away",
    "strength_defence_home",
    "strength_defence_away",
)


def _is_na(v) -> bool:
    if v is None:
        return True
    if isinstance(v, (float, int)) and not isinstance(v, bool):
        if isinstance(v, float) and (math.isnan(v) or math.isinf(v)):
            return True
    try:
        return bool(pd.isna(v))
    except (ValueError, TypeError):
        return False


def _as_int(v):
    if _is_na(v):
        return None
    return int(round(float(v)))


def _as_round2(v):
    if _is_na(v):
        return None
    return round(float(v), 2)


def _as_str(v):
    if _is_na(v):
        return None
    text = str(v).strip()
    return text or None


def _kickoff_iso(v) -> str | None:
    if _is_na(v):
        return None
    if isinstance(v, datetime):
        if v.tzinfo is None:
            v = v.replace(tzinfo=timezone.utc)
        return v.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(v, date) and not isinstance(v, datetime):
        return f"{v.isoformat()}T00:00:00Z"
    text = str(v).strip().replace(" ", "T")
    if text.endswith("+00:00"):
        text = text[:-6] + "Z"
    return text or None


def _load_strengths() -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    csv = load_teams_csv()
    if not csv.empty and "code" in csv.columns:
        keep = ["season", "code", *STRENGTH_KEYS]
        for col in keep:
            if col not in csv.columns:
                csv[col] = pd.NA
        frames.append(csv[keep].copy())
    _, bootstrap = latest_bootstrap()
    current = season_from_bootstrap(bootstrap)
    boot_rows = []
    for team in bootstrap.get("teams") or []:
        row = {"season": current, "code": team.get("code")}
        for key in STRENGTH_KEYS:
            row[key] = team.get(key)
        boot_rows.append(row)
    if boot_rows:
        frames.append(pd.DataFrame(boot_rows))
    if not frames:
        return pd.DataFrame(columns=["season", "code", *STRENGTH_KEYS])
    out = pd.concat(frames, ignore_index=True, sort=False)
    out["code"] = pd.to_numeric(out["code"], errors="coerce").astype("Int64")
    out["season"] = out["season"].astype(str)
    return out.dropna(subset=["season", "code"]).drop_duplicates(
        subset=["season", "code"], keep="last"
    )


def build_fixtures_by_season(fx: pd.DataFrame) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    if fx.empty:
        return out
    work = fx.copy()
    work["season"] = work["season"].astype(str)
    for season, block in work.groupby("season", sort=True):
        rows = []
        for rec in block.to_dict("records"):
            hs, aws = parse_scoreline(rec.get("result"))
            rows.append(
                {
                    "id": _as_int(rec.get("fixture_id")),
                    "gw": _as_int(rec.get("gw")),
                    "kickoff": _kickoff_iso(rec.get("kickoff")),
                    "home": _as_int(rec.get("home_team_code")),
                    "away": _as_int(rec.get("away_team_code")),
                    "home_score": hs,
                    "away_score": aws,
                    "finished": hs is not None and aws is not None,
                    "home_difficulty": _as_int(rec.get("home_difficulty")),
                    "away_difficulty": _as_int(rec.get("away_difficulty")),
                }
            )
        rows.sort(
            key=lambda r: (
                r["gw"] is None,
                r["gw"] or 0,
                r["kickoff"] or "",
                r["id"] or 0,
            )
        )
        out[str(season)] = rows
    return out


def build_teams_by_season(
    fx: pd.DataFrame,
    team_gw: pd.DataFrame,
    dim_team: pd.DataFrame,
) -> dict[str, dict[str, dict]]:
    strengths = _load_strengths()
    names = dim_team.copy()
    names["season"] = names["season"].astype(str)
    names["code"] = pd.to_numeric(names["code"], errors="coerce").astype("Int64")

    xg_for: dict[tuple[str, int], float] = {}
    xg_against: dict[tuple[str, int], float] = {}
    if not team_gw.empty:
        tg = team_gw.copy()
        tg["season"] = tg["season"].astype(str)
        tg["team_code"] = pd.to_numeric(tg["team_code"], errors="coerce").astype("Int64")
        for rec in tg.to_dict("records"):
            code = rec.get("team_code")
            if pd.isna(code):
                continue
            key = (str(rec["season"]), int(code))
            xg = rec.get("xg")
            xgc = rec.get("xgc")
            if not _is_na(xg):
                xg_for[key] = xg_for.get(key, 0.0) + float(xg)
            if not _is_na(xgc):
                xg_against[key] = xg_against.get(key, 0.0) + float(xgc)

    records: dict[tuple[str, int], dict] = {}
    if not fx.empty:
        work = fx.copy()
        work["season"] = work["season"].astype(str)
        work["home_team_code"] = pd.to_numeric(work["home_team_code"], errors="coerce")
        work["away_team_code"] = pd.to_numeric(work["away_team_code"], errors="coerce")
        for rec in work.to_dict("records"):
            hs, aws = parse_scoreline(rec.get("result"))
            if hs is None or aws is None:
                continue
            season = str(rec["season"])
            home = rec.get("home_team_code")
            away = rec.get("away_team_code")
            if pd.isna(home) or pd.isna(away):
                continue
            sides = (
                (int(home), hs, aws),
                (int(away), aws, hs),
            )
            for code, gf, ga in sides:
                row = records.setdefault(
                    (season, code),
                    {
                        "matches": 0,
                        "wins": 0,
                        "draws": 0,
                        "losses": 0,
                        "goals_for": 0,
                        "goals_against": 0,
                        "clean_sheets": 0,
                    },
                )
                row["matches"] += 1
                row["goals_for"] += gf
                row["goals_against"] += ga
                if ga == 0:
                    row["clean_sheets"] += 1
                if gf > ga:
                    row["wins"] += 1
                elif gf == ga:
                    row["draws"] += 1
                else:
                    row["losses"] += 1

    out: dict[str, dict[str, dict]] = {}
    roster = names.loc[names["season"] >= TEAMS_FROM_SEASON]
    for rec in roster.to_dict("records"):
        season = str(rec["season"])
        code = rec.get("code")
        if pd.isna(code):
            continue
        code_i = int(code)
        stats = records.get((season, code_i), {
            "matches": 0,
            "wins": 0,
            "draws": 0,
            "losses": 0,
            "goals_for": 0,
            "goals_against": 0,
            "clean_sheets": 0,
        })
        matches = int(stats["matches"])
        gf = int(stats["goals_for"])
        ga = int(stats["goals_against"])
        payload = {
            "name": _as_str(rec.get("name")),
            "short_name": _as_str(rec.get("short_name")),
            "matches": matches,
            "wins": int(stats["wins"]),
            "draws": int(stats["draws"]),
            "losses": int(stats["losses"]),
            "points": 3 * int(stats["wins"]) + int(stats["draws"]),
            "goals_for": gf,
            "goals_against": ga,
            "goal_difference": gf - ga,
            "xg_for": None,
            "xg_against": None,
            "clean_sheets": int(stats["clean_sheets"]),
            "xg_for_per_match": None,
            "xg_against_per_match": None,
        }
        if season >= XG_FROM_SEASON:
            xf = xg_for.get((season, code_i))
            xa = xg_against.get((season, code_i))
            payload["xg_for"] = _as_round2(xf)
            payload["xg_against"] = _as_round2(xa)
            if matches:
                payload["xg_for_per_match"] = _as_round2(
                    None if xf is None else xf / matches
                )
                payload["xg_against_per_match"] = _as_round2(
                    None if xa is None else xa / matches
                )
        strength_row = strengths.loc[
            (strengths["season"] == season) & (strengths["code"] == code_i)
        ]
        for key in STRENGTH_KEYS:
            val = None
            if not strength_row.empty:
                val = strength_row.iloc[0].get(key)
            payload[key] = _as_int(val)
        out.setdefault(season, {})[str(code_i)] = payload
    return out
