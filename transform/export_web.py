"""Turn marts parquet into JSON that web/index.html reads from web/data/."""

from __future__ import annotations

import json
import math
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd

from ingest.paths import MARTS, REPO_ROOT
from transform.explain import attach_explain, load_explain_index
from transform.metric_copy import OPTA_COUNTS, OPTA_RATIOS, STYLE_COPY
from transform.web_price import (
    NULL_PRICE_SEASONS,
    PRICE_FROM_SEASON,
    build_pricehistory,
    load_gw_deadlines,
    load_snap_frame,
    matchlog_prices,
    season_price_columns,
    validate_pricehistory,
    _null_season_cols,
)

WEB_DATA = REPO_ROOT / "web" / "data"

TABLES = (
    "fact_player_season_metrics",
    "fact_player_season_availability",
    "fact_player_gw",
    "fact_player_fixture",
    "fact_team_gw",
    "fact_fixture",
    "dim_player",
    "dim_team",
    "dim_region",
    "snap_player_day",
    "fact_player_style",
    "dim_style_cluster",
    "fact_player_cluster",
    "fact_player_season_opta",
)

POSITION_LABEL = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}

# JSON key -> mart column holding the player season total.
# influence/creativity/threat/ict are summed from fact_player_gw (not in the metrics mart).
TOTAL_MAP = {
    "goals": "goals_total",
    "assists": "assists_total",
    "xg": "xg_total",
    "xa": "xa_total",
    "xgi": "xgi_total",
    "tackles": "tackles_total",
    "recoveries": "recoveries_total",
    "cbi": "clearances_blocks_interceptions_total",
    "defcon": "defensive_contribution_total",
    "saves": "saves_total",
    "gc": "goals_conceded_total",
    "xgc": "xgc_total",
    "cs": "clean_sheets_total",
    "pensaved": "penalties_saved_total",
    "points": "total_points_total",
    "bps": "bps_total",
    "bonus": "bonus_total",
    "influence": "influence_total",
    "creativity": "creativity_total",
    "threat": "threat_total",
    "ict": "ict_total",
    "yellow": "yellow_cards_total",
    "red": "red_cards_total",
    "og": "own_goals_total",
    "penmissed": "penalties_missed_total",
}

# JSON {key}_team <- fact_team_gw column, summed over GWs the player was at the club.
TEAM_MAP = {
    "goals": "goals",
    "assists": "assists",
    "xg": "xg",
    "xa": "xa",
    "xgi": "xgi",
    "tackles": "tackles",
    "recoveries": "recoveries",
    "cbi": "clearances_blocks_interceptions",
    "defcon": "defensive_contribution",
}

# Match-log JSON key -> fact_player_fixture column. Same keys as TOTAL_MAP.
GW_METRIC_MAP = {
    "goals": "goals",
    "assists": "assists",
    "xg": "expected_goals",
    "xa": "expected_assists",
    "xgi": "expected_goal_involvements",
    "tackles": "tackles",
    "recoveries": "recoveries",
    "cbi": "clearances_blocks_interceptions",
    "defcon": "defensive_contribution",
    "saves": "saves",
    "gc": "goals_conceded",
    "xgc": "expected_goals_conceded",
    "cs": "clean_sheets",
    "pensaved": "penalties_saved",
    "points": "total_points",
    "bps": "bps",
    "bonus": "bonus",
    "influence": "influence",
    "creativity": "creativity",
    "threat": "threat",
    "ict": "ict_index",
    "yellow": "yellow",
    "red": "red",
    "og": "own_goals",
    "penmissed": "penalties_missed",
}

GW_METRIC_INT = {
    "goals",
    "assists",
    "tackles",
    "recoveries",
    "cbi",
    "defcon",
    "saves",
    "gc",
    "cs",
    "pensaved",
    "points",
    "bps",
    "bonus",
    "yellow",
    "red",
    "og",
    "penmissed",
}

# element_type values the metric applies to. Absent = every position.
METRIC_POS = {
    "saves": {1},
    "gc": {1},
    "pensaved": {1},
    "cs": {1, 2},
    "penmissed": {2, 3, 4},
}

# Null throughout seasons before the feed published the stat.
METRIC_FROM_SEASON = {
    "xg": "2022-23",
    "xa": "2022-23",
    "xgi": "2022-23",
    "xgc": "2022-23",
    "defcon": "2025-26",
}

KEEP_JSON = {"manifest.json", "clusters.json", "metrics_register.json"}


def _parquet(name: str) -> str:
    return (MARTS / f"{name}.parquet").as_posix()


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


def _birth_date(v):
    if _is_na(v):
        return None
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    text = str(v).strip()
    if not text:
        return None
    return text[:10]


def _web_name(rec: dict) -> str | None:
    name = _as_str(rec.get("web_name"))
    if name:
        return name
    first = _as_str(rec.get("first_name")) or ""
    second = _as_str(rec.get("second_name")) or ""
    joined = f"{first} {second}".strip()
    return joined or None


def _iso_date(v):
    if _is_na(v):
        return None
    if isinstance(v, datetime):
        return v.date().isoformat()
    if isinstance(v, date):
        return v.isoformat()
    text = str(v).strip()
    if len(text) >= 10:
        return text[:10]
    return None


def _parse_scores(result, home):
    if home is None or _is_na(result):
        return None, None
    parts = str(result).strip().split("-")
    if len(parts) != 2:
        return None, None
    try:
        home_n, away_n = int(parts[0]), int(parts[1])
    except (TypeError, ValueError):
        return None, None
    if home:
        return home_n, away_n
    return away_n, home_n


def _metric_live(key: str, season: str) -> bool:
    start = METRIC_FROM_SEASON.get(key)
    return start is None or season >= start


def load_rows(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    metrics = _parquet("fact_player_season_metrics")
    avail = _parquet("fact_player_season_availability")
    gw = _parquet("fact_player_gw")
    team_gw = _parquet("fact_team_gw")
    players = _parquet("dim_player")
    teams = _parquet("dim_team")
    regions = _parquet("dim_region")
    team_select = ",\n         ".join(
        f"SUM(t.{col}) AS {key}_team" for key, col in TEAM_MAP.items()
    )
    frame = con.execute(
        f"""
        WITH season_m AS (
            SELECT * EXCLUDE (team_code)
            FROM read_parquet('{metrics}')
            WHERE grain = 'season'
        ),
        season_a AS (
            SELECT
                season,
                player_code,
                minutes,
                appearances,
                starts,
                appearances_60_plus,
                team_minutes_available
            FROM read_parquet('{avail}')
            WHERE grain = 'season'
        ),
        last_team AS (
            SELECT season, player_code, team_code
            FROM (
                SELECT
                    season,
                    player_code,
                    team_code,
                    ROW_NUMBER() OVER (
                        PARTITION BY season, player_code
                        ORDER BY gw DESC NULLS LAST
                    ) AS rn
                FROM read_parquet('{gw}')
            )
            WHERE rn = 1
        ),
        ict AS (
            SELECT
                season,
                player_code,
                SUM(influence) AS influence_total,
                SUM(creativity) AS creativity_total,
                SUM(threat) AS threat_total,
                SUM(ict_index) AS ict_total
            FROM read_parquet('{gw}')
            GROUP BY 1, 2
        ),
        team_tot AS (
            SELECT
                p.season,
                p.player_code,
                {team_select}
            FROM read_parquet('{gw}') p
            LEFT JOIN read_parquet('{team_gw}') t
              ON t.season = p.season
             AND t.gw = p.gw
             AND t.team_code = p.team_code
            GROUP BY 1, 2
        )
        SELECT
            m.*,
            a.minutes,
            a.appearances,
            a.starts,
            a.appearances_60_plus,
            a.team_minutes_available,
            p.web_name,
            p.first_name,
            p.second_name,
            p.birth_date,
            r.country_name AS nationality,
            tm.name AS team_name,
            i.influence_total,
            i.creativity_total,
            i.threat_total,
            i.ict_total,
            tt.* EXCLUDE (season, player_code)
        FROM season_m m
        LEFT JOIN season_a a
          ON a.season = m.season AND a.player_code = m.player_code
        LEFT JOIN read_parquet('{players}') p
          ON p.code = m.player_code
        LEFT JOIN read_parquet('{regions}') r
          ON r.region_id = p.region
        LEFT JOIN last_team lt
          ON lt.season = m.season AND lt.player_code = m.player_code
        LEFT JOIN read_parquet('{teams}') tm
          ON tm.season = lt.season AND tm.code = lt.team_code
        LEFT JOIN ict i
          ON i.season = m.season AND i.player_code = m.player_code
        LEFT JOIN team_tot tt
          ON tt.season = m.season AND tt.player_code = m.player_code
        """
    ).df()
    return _attach_opta_and_style(frame)


def _parquet_cols(path: Path, wanted: list[str]) -> list[str]:
    import pyarrow.parquet as pq

    names = set(pq.read_schema(path).names)
    return [c for c in wanted if c in names]


def _attach_opta_and_style(frame: pd.DataFrame) -> pd.DataFrame:
    def _keys(df: pd.DataFrame) -> pd.DataFrame:
        out = df.copy()
        out["season"] = out["season"].astype(str)
        out["player_code"] = pd.to_numeric(out["player_code"], errors="coerce").astype("Int64")
        return out

    frame = _keys(frame)
    opta_path = MARTS / "fact_player_season_opta.parquet"
    if opta_path.exists():
        wanted = ["season", "player_code", *OPTA_COUNTS, *OPTA_RATIOS]
        cols = _parquet_cols(opta_path, wanted)
        opta = _keys(pd.read_parquet(opta_path, columns=cols))
        rename = {c: f"o_{c}" for c in opta.columns if c not in {"season", "player_code"}}
        opta = opta.rename(columns=rename)
        frame = frame.merge(opta, on=["season", "player_code"], how="left")
    style_path = MARTS / "fact_player_style.parquet"
    if style_path.exists():
        style_names = [n for n in STYLE_COPY if n not in OPTA_RATIOS]
        wanted = ["season", "player_code", *style_names]
        cols = _parquet_cols(style_path, wanted)
        style = _keys(pd.read_parquet(style_path, columns=cols))
        rename = {c: f"o_{c}" for c in style.columns if c not in {"season", "player_code"}}
        style = style.rename(columns=rename)
        frame = frame.merge(style, on=["season", "player_code"], how="left")
    return frame


def build_season_rows(
    frame: pd.DataFrame, price_cols: dict[tuple[str, int], dict] | None = None
) -> dict[str, list[dict]]:
    price_cols = price_cols or {}
    by_season: dict[str, list[dict]] = {}
    dropped_minutes = 0
    print("season export (drop 0 appearances)", flush=True)
    counts: dict[str, list[int]] = {}
    for rec in frame.to_dict("records"):
        season = str(rec["season"])
        counts.setdefault(season, [0, 0])
        apps = _as_int(rec.get("appearances")) or 0
        mins = _as_int(rec.get("minutes")) or 0
        if apps <= 0:
            counts[season][1] += 1
            if mins > 0:
                dropped_minutes += 1
            continue
        counts[season][0] += 1
        pos_n = _as_int(rec.get("element_type"))
        row = {
            "player_code": _as_int(rec.get("player_code")),
            "web_name": _web_name(rec),
            "first_name": _as_str(rec.get("first_name")),
            "second_name": _as_str(rec.get("second_name")),
            "team_name": _as_str(rec.get("team_name")),
            "position": POSITION_LABEL.get(pos_n) if pos_n is not None else None,
            "nationality": _as_str(rec.get("nationality")),
            "birth_date": _birth_date(rec.get("birth_date")),
            "minutes": _as_int(rec.get("minutes")),
            "appearances": _as_int(rec.get("appearances")),
            "starts": _as_int(rec.get("starts")),
            "apps_60": _as_int(rec.get("appearances_60_plus")),
            "team_minutes": _as_int(rec.get("team_minutes_available")),
            "moved": (not _is_na(rec.get("spell_count"))) and int(rec["spell_count"]) > 1,
        }
        for key, col in TOTAL_MAP.items():
            row[f"{key}_total"] = (
                _as_round2(rec.get(col)) if _metric_live(key, season) else None
            )
        for key, col in TEAM_MAP.items():
            row[f"{key}_team"] = (
                _as_round2(rec.get(f"{key}_team")) if _metric_live(key, season) else None
            )
        priced = _null_season_cols()
        if season >= PRICE_FROM_SEASON and row["player_code"] is not None:
            priced.update(price_cols.get((season, row["player_code"]), {}))
        row.update(priced)
        for col in OPTA_COUNTS:
            row[f"o_{col}_total"] = _as_int(rec.get(f"o_{col}"))
        for col in OPTA_RATIOS:
            row[f"o_{col}"] = _as_round4(rec.get(f"o_{col}"))
        for name in STYLE_COPY:
            if name in OPTA_RATIOS:
                continue
            row[f"o_{name}"] = _as_round4(rec.get(f"o_{name}"))
        by_season.setdefault(season, []).append(row)

    for rows in by_season.values():
        rows.sort(key=lambda r: (-(r.get("minutes") or 0), r.get("web_name") or ""))
    for season in sorted(counts):
        kept, dropped = counts[season]
        print(f"  {season}  kept {kept:4}  dropped {dropped:4}", flush=True)
    print(f"  0-appearance rows with minutes>0: {dropped_minutes}", flush=True)
    if dropped_minutes:
        raise RuntimeError(
            f"{dropped_minutes} player-seasons with minutes>0 were dropped as 0-appearance"
        )
    return by_season


def _played_matchlog_rows(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    pf = _parquet("fact_player_fixture")
    fx = _parquet("fact_fixture")
    teams = _parquet("dim_team")
    avail = _parquet("fact_player_season_availability")
    return con.execute(
        f"""
        SELECT
            p.season,
            p.gw,
            p.player_code,
            p.fixture_id,
            FALSE AS blank,
            p.opponent,
            CASE
                WHEN p.team_code = fx.home_team_code THEN TRUE
                WHEN p.team_code = fx.away_team_code THEN FALSE
                ELSE p.was_home
            END AS was_home,
            p.minutes,
            p.starts,
            p.goals,
            p.assists,
            p.expected_goals,
            p.expected_assists,
            p.expected_goal_involvements,
            p.tackles,
            p.recoveries,
            p.clearances_blocks_interceptions,
            p.defensive_contribution,
            p.saves,
            p.goals_conceded,
            p.expected_goals_conceded,
            p.clean_sheets,
            p.penalties_saved,
            p.yellow,
            p.red,
            p.own_goals,
            p.penalties_missed,
            p.total_points,
            p.bps,
            p.bonus,
            p.influence,
            p.creativity,
            p.threat,
            p.ict_index,
            CASE
                WHEN p.team_code = fx.home_team_code THEN away.short_name
                WHEN p.team_code = fx.away_team_code THEN home.short_name
                ELSE opp.short_name
            END AS opp_short,
            fx.kickoff,
            fx.result,
            a.element_type
        FROM read_parquet('{pf}') p
        LEFT JOIN read_parquet('{fx}') fx
          ON fx.season = p.season AND fx.fixture_id = p.fixture_id
        LEFT JOIN read_parquet('{teams}') home
          ON home.season = fx.season AND home.code = fx.home_team_code
        LEFT JOIN read_parquet('{teams}') away
          ON away.season = fx.season AND away.code = fx.away_team_code
        LEFT JOIN read_parquet('{teams}') opp
          ON opp.season = p.season AND opp.code = p.opponent
        LEFT JOIN read_parquet('{avail}') a
          ON a.grain = 'season'
         AND a.season = p.season
         AND a.player_code = p.player_code
        WHERE p.player_code IS NOT NULL
        """
    ).df()


def _blank_matchlog_rows(con: duckdb.DuckDBPyConnection, template: pd.DataFrame) -> pd.DataFrame:
    """GWs in a player's min–max span where their club had no fixture."""
    gw = _parquet("fact_player_gw")
    fx = _parquet("fact_fixture")
    avail = _parquet("fact_player_season_availability")
    spans = con.execute(
        f"""
        SELECT season, player_code, MIN(gw) AS min_gw, MAX(gw) AS max_gw
        FROM read_parquet('{gw}')
        WHERE player_code IS NOT NULL AND gw IS NOT NULL
        GROUP BY 1, 2
        """
    ).df()
    teams = con.execute(
        f"""
        SELECT season, gw, player_code, team_code
        FROM read_parquet('{gw}')
        WHERE player_code IS NOT NULL
        """
    ).df()
    club = con.execute(
        f"""
        SELECT DISTINCT season, gw, team_code
        FROM (
            SELECT season, gw, home_team_code AS team_code
            FROM read_parquet('{fx}')
            UNION ALL
            SELECT season, gw, away_team_code
            FROM read_parquet('{fx}')
        )
        WHERE team_code IS NOT NULL AND gw IS NOT NULL
        """
    ).df()
    etype = con.execute(
        f"""
        SELECT season, player_code, element_type
        FROM read_parquet('{avail}')
        WHERE grain = 'season'
        """
    ).df()
    if spans.empty:
        return template.iloc[0:0].copy()

    spans = spans.copy()
    spans["gw"] = spans.apply(
        lambda r: list(range(int(r["min_gw"]), int(r["max_gw"]) + 1)), axis=1
    )
    calendar = spans.explode("gw", ignore_index=True)
    calendar["gw"] = pd.to_numeric(calendar["gw"], errors="coerce")
    teams = teams.copy()
    teams["gw"] = pd.to_numeric(teams["gw"], errors="coerce")
    filled = calendar.merge(
        teams[["season", "player_code", "gw", "team_code"]],
        on=["season", "player_code", "gw"],
        how="left",
    )
    filled = filled.sort_values(["season", "player_code", "gw"])
    filled["team_code"] = filled.groupby(["season", "player_code"], sort=False)[
        "team_code"
    ].ffill()
    filled["team_code"] = filled.groupby(["season", "player_code"], sort=False)[
        "team_code"
    ].bfill()
    filled = filled.dropna(subset=["team_code", "gw"])
    filled["team_code"] = pd.to_numeric(filled["team_code"], errors="coerce")
    filled["gw"] = pd.to_numeric(filled["gw"], errors="coerce")
    club = club.copy()
    club["gw"] = pd.to_numeric(club["gw"], errors="coerce")
    club["team_code"] = pd.to_numeric(club["team_code"], errors="coerce")
    club["has_fx"] = True
    blanks = filled.merge(
        club, on=["season", "gw", "team_code"], how="left"
    )
    blanks = blanks.loc[blanks["has_fx"].isna()].drop(
        columns=["has_fx", "min_gw", "max_gw"], errors="ignore"
    )
    if blanks.empty:
        return template.iloc[0:0].copy()
    blanks = blanks.merge(etype, on=["season", "player_code"], how="left")
    rows = {col: pd.NA for col in template.columns}
    rows.update(
        {
            "season": blanks["season"].astype(str).to_numpy(),
            "gw": blanks["gw"].to_numpy(),
            "player_code": blanks["player_code"].to_numpy(),
            "fixture_id": pd.array([pd.NA] * len(blanks), dtype="Int64"),
            "blank": True,
            "element_type": blanks["element_type"].to_numpy(),
        }
    )
    return pd.DataFrame(rows)[list(template.columns)]


def load_matchlog_rows(con: duckdb.DuckDBPyConnection) -> pd.DataFrame:
    played = _played_matchlog_rows(con)
    blanks = _blank_matchlog_rows(con, played)
    out = pd.concat([played, blanks], ignore_index=True)
    return out.sort_values(
        ["season", "player_code", "gw", "fixture_id"], na_position="last"
    ).reset_index(drop=True)


def _gw_metric_value(key: str, rec: dict, season: str, pos: int | None):
    allowed = METRIC_POS.get(key)
    if allowed is not None and (pos is None or pos not in allowed):
        return None
    if not _metric_live(key, season):
        return None
    raw = rec.get(GW_METRIC_MAP[key])
    if key in GW_METRIC_INT:
        return _as_int(raw)
    return _as_round2(raw)


def _matchlog_price_fields(
    gw_prices: dict[tuple[str, int, int], dict],
    season: str,
    code: int,
    gw: int | None,
) -> dict:
    if season in NULL_PRICE_SEASONS or season < PRICE_FROM_SEASON or gw is None:
        return {"price": None, "price_delta": None}
    return gw_prices.get((season, code, gw), {"price": None, "price_delta": None})


def build_matchlogs(
    frame: pd.DataFrame,
    gw_prices: dict[tuple[str, int, int], dict] | None = None,
    explain_lookup: dict | None = None,
    live_gws: set | None = None,
) -> dict[str, dict[str, list[dict]]]:
    gw_prices = gw_prices or {}
    explain_lookup = explain_lookup or {}
    live_gws = live_gws or set()
    by_season: dict[str, dict[str, list[dict]]] = {}
    for rec in frame.to_dict("records"):
        season = str(rec["season"])
        code = _as_int(rec.get("player_code"))
        if code is None:
            continue
        blank_v = rec.get("blank")
        blank = False if _is_na(blank_v) else bool(blank_v)
        gw = _as_int(rec.get("gw"))
        if blank:
            row = {
                "gw": gw,
                "fixture": None,
                "blank": True,
                "date": None,
                "opp": None,
                "home": None,
                "team_score": None,
                "opp_score": None,
                "minutes": None,
                "started": None,
            }
            for key in GW_METRIC_MAP:
                row[key] = None
            row.update(_matchlog_price_fields(gw_prices, season, code, gw))
            by_season.setdefault(season, {}).setdefault(str(code), []).append(row)
            continue
        home_v = rec.get("was_home")
        home = None if _is_na(home_v) else bool(home_v)
        starts = rec.get("starts")
        started = None if _is_na(starts) else int(starts) >= 1
        team_score, opp_score = _parse_scores(rec.get("result"), home)
        pos = _as_int(rec.get("element_type"))
        opp = (
            None
            if _is_na(rec.get("opp_short"))
            else _as_str(rec.get("opp_short"))
        )
        minutes = _as_int(rec.get("minutes"))
        row = {
            "gw": gw,
            "fixture": _as_int(rec.get("fixture_id")),
            "blank": False,
            "date": _iso_date(rec.get("kickoff")),
            "opp": opp,
            "home": home,
            "team_score": team_score,
            "opp_score": opp_score,
            "minutes": 0 if minutes is None else minutes,
            "started": started,
        }
        for key in GW_METRIC_MAP:
            row[key] = _gw_metric_value(key, rec, season, pos)
        if row["points"] is None and row["minutes"] == 0:
            row["points"] = 0
        row.update(_matchlog_price_fields(gw_prices, season, code, gw))
        attach_explain(
            row,
            season=season,
            gw=gw,
            player_code=code,
            fixture_id=row["fixture"],
            lookup=explain_lookup,
            live_gws=live_gws,
        )
        by_season.setdefault(season, {}).setdefault(str(code), []).append(row)
    return by_season


def _matchlog_coverage(frame: pd.DataFrame) -> list[dict]:
    rows = []
    played = frame.loc[~frame["blank"].fillna(False).astype(bool)] if "blank" in frame.columns else frame
    for season, grp in frame.groupby("season", sort=True):
        gplay = played.loc[played["season"] == season] if "season" in played.columns else grp
        n_blank = int(grp["blank"].fillna(False).astype(bool).sum()) if "blank" in grp.columns else 0
        sgw = gplay["was_home"].notna()
        n_sgw = int(sgw.sum())
        n_unknown = int((~sgw).sum())
        missing_opp = int((sgw & gplay["opp_short"].isna()).sum())
        missing_score = int((sgw & gplay["result"].isna()).sum())
        missing_date = int(gplay["kickoff"].isna().sum()) if "kickoff" in gplay.columns else 0
        missing_fixture = int(gplay["fixture_id"].isna().sum()) if "fixture_id" in gplay.columns else 0
        dgw_keys = (
            gplay.groupby(["player_code", "gw"]).size().reset_index(name="n")
            if not gplay.empty else pd.DataFrame(columns=["n"])
        )
        n_dgw = int((dgw_keys["n"] > 1).sum()) if not dgw_keys.empty else 0
        rows.append(
            {
                "season": str(season),
                "rows": int(len(grp)),
                "players": int(grp["player_code"].nunique()),
                "gws": int(grp["gw"].nunique()),
                "blank_rows": n_blank,
                "dgw_player_gws": n_dgw,
                "sgw_rows": n_sgw,
                "unknown_opp_rows": n_unknown,
                "sgw_missing_opp_code": missing_opp,
                "sgw_missing_score": missing_score,
                "missing_date": missing_date,
                "missing_fixture": missing_fixture,
            }
        )
    return rows


def _as_round4(v):
    if _is_na(v):
        return None
    return round(float(v), 4)


def export_style_json(keep: set[str]) -> None:
    """clusters.json plus style_{season}.json for seasons with assignments."""
    from transform.load import load_style_features
    from transform.style import CLUSTER_NOTE_LIST, clustering_feature_names

    dim = pd.read_parquet(MARTS / "dim_style_cluster.parquet")
    fact = pd.read_parquet(MARTS / "fact_player_cluster.parquet")
    style = pd.read_parquet(MARTS / "fact_player_style.parquet")
    feats = clustering_feature_names(load_style_features())
    zcols = [f"{n}_z" for n in feats]

    clusters = []
    for _, row in dim.sort_values("cluster_id").iterrows():
        centroid = [_as_round4(row[name]) for name in feats]
        if any(v is None for v in centroid):
            raise RuntimeError(f"cluster {int(row['cluster_id'])} has a null centroid")
        clusters.append(
            {
                "cluster_id": int(row["cluster_id"]),
                "label": str(row["label"]),
                "description": str(row["description"]),
                "n": int(row["n_player_seasons"]),
                "centroid": centroid,
            }
        )
    catalog = {"features": feats, "notes": list(CLUSTER_NOTE_LIST), "clusters": clusters}
    nbytes, changed = _write_json(WEB_DATA / "clusters.json", catalog)
    flag = "" if changed else "  unchanged"
    print(f"  {'clusters.json':28} {nbytes:8,} bytes  {len(clusters)} clusters{flag}", flush=True)
    keep.add("clusters.json")

    joined = fact.merge(style, on=["season", "player_code"], how="left")
    print("web/data style", flush=True)
    for season in sorted(joined["season"].astype(str).unique()):
        name = f"style_{season}.json"
        keep.add(name)
        payload = {}
        block = joined.loc[joined["season"].astype(str) == season]
        for rec in block.to_dict(orient="records"):
            z = [_as_round4(rec[col]) for col in zcols]
            if any(v is None for v in z):
                raise RuntimeError(
                    f"incomplete clustering z-vector for {season} {rec['player_code']}"
                )
            payload[str(int(rec["player_code"]))] = {
                "z": z,
                "c": int(rec["cluster_id"]),
                "d": _as_round4(rec["distance"]),
                "c2": int(rec["second_cluster_id"]),
                "d2": _as_round4(rec["second_distance"]),
            }
        nbytes, changed = _write_json(WEB_DATA / name, payload)
        flag = "" if changed else "  unchanged"
        print(f"  {name:28} {nbytes:8,} bytes  {len(payload):4} players{flag}", flush=True)


def _write_json(path: Path, obj) -> tuple[int, bool]:
    encoded = json.dumps(obj, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode("utf-8")
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and path.read_bytes() == encoded:
        return len(encoded), False
    path.write_bytes(encoded)
    return len(encoded), True


def _confirm_xa_source(con: duckdb.DuckDBPyConnection) -> None:
    metrics = _parquet("fact_player_season_metrics")
    gw = _parquet("fact_player_gw")
    chk = con.execute(
        f"""
        WITH gw AS (
            SELECT season, player_code, SUM(expected_assists) AS xa_gw
            FROM read_parquet('{gw}')
            GROUP BY 1, 2
        )
        SELECT
            COUNT(*) FILTER (
                WHERE abs(COALESCE(m.xa_total, 0) - COALESCE(g.xa_gw, 0)) > 1e-6
            ) AS mismatch,
            MAX(abs(COALESCE(m.xa_total, 0) - COALESCE(g.xa_gw, 0))) AS max_abs
        FROM read_parquet('{metrics}') m
        LEFT JOIN gw g USING (season, player_code)
        WHERE m.grain = 'season'
        """
    ).df().iloc[0]
    n = int(chk["mismatch"])
    mx = chk["max_abs"]
    print(
        f"xa_total vs SUM(fact_player_gw.expected_assists): "
        f"mismatches={n} max_abs={mx}",
        flush=True,
    )
    if n:
        raise RuntimeError(
            "xa_total is not the sum of FPL expected_assists "
            f"({n} mismatches, max_abs={mx})"
        )


def export() -> dict:
    missing = [n for n in TABLES if not (MARTS / f"{n}.parquet").exists()]
    if missing:
        raise FileNotFoundError(
            "Missing marts: " + ", ".join(missing) + ". Run `make marts` from the repo root."
        )

    con = duckdb.connect(database=":memory:")
    try:
        frame = load_rows(con)
        log_frame = load_matchlog_rows(con)
        _confirm_xa_source(con)
        print("loading snap_player_day for price/own...", flush=True)
        snaps = load_snap_frame(con)
        print(f"  snap rows {len(snaps)}", flush=True)
    finally:
        con.close()

    print("gw deadlines", flush=True)
    deadlines = load_gw_deadlines()
    price_cols = season_price_columns(snaps)
    gw_keys = log_frame[["season", "player_code", "gw"]].drop_duplicates()
    gw_prices = matchlog_prices(snaps, deadlines, gw_keys)
    histories = build_pricehistory(snaps)

    map_path = MARTS / "dim_season_map.parquet"
    player_map: dict[tuple[str, int], int] = {}
    if map_path.exists():
        sm = pd.read_parquet(map_path, columns=["season", "element_id", "player_code"])
        sm = sm.dropna(subset=["season", "element_id", "player_code"])
        for rec in sm.to_dict("records"):
            try:
                player_map[(str(rec["season"]), int(rec["element_id"]))] = int(rec["player_code"])
            except (TypeError, ValueError):
                continue
    explain_lookup, live_gws = load_explain_index(player_map)
    if live_gws:
        by_s: dict[str, list[int]] = {}
        for season, gw in sorted(live_gws):
            by_s.setdefault(season, []).append(gw)
        print("live explain coverage", flush=True)
        for season, gws in by_s.items():
            print(f"  {season}  gws={len(gws)}  ({min(gws)}-{max(gws)})  blocks={sum(1 for k in explain_lookup if k[0]==season)}", flush=True)
    else:
        print("live explain coverage: none (vaastav-only matchlogs)", flush=True)

    by_season = build_season_rows(frame, price_cols)
    matchlogs = build_matchlogs(log_frame, gw_prices, explain_lookup, live_gws)
    keep_codes = {
        season: {str(r["player_code"]) for r in rows if r.get("player_code") is not None}
        for season, rows in by_season.items()
    }
    matchlogs = {
        season: {code: rows for code, rows in players.items() if code in keep_codes.get(season, set())}
        for season, players in matchlogs.items()
    }
    histories = {
        season: {
            code: obj
            for code, obj in players.items()
            if code in keep_codes.get(season, set())
        }
        for season, players in histories.items()
        if season >= PRICE_FROM_SEASON
    }
    histories = {season: players for season, players in histories.items() if players}
    validate_pricehistory(histories, price_cols, by_season, matchlogs)
    coverage = _matchlog_coverage(log_frame)
    WEB_DATA.mkdir(parents=True, exist_ok=True)

    keep = set(KEEP_JSON)
    season_meta = []
    print("web/data export", flush=True)
    for season in sorted(by_season.keys(), reverse=True):
        name = f"players_{season}.json"
        keep.add(name)
        rows = by_season[season]
        nbytes, changed = _write_json(WEB_DATA / name, rows)
        season_meta.append({"season": season, "rows": len(rows)})
        flag = "" if changed else "  unchanged"
        print(f"  {name:28} {nbytes:8,} bytes  {len(rows):4} rows{flag}", flush=True)

    print("web/data matchlogs", flush=True)
    too_big = []
    for season in sorted(matchlogs.keys(), reverse=True):
        if season not in by_season:
            continue
        name = f"matchlogs_{season}.json"
        keep.add(name)
        payload = matchlogs[season]
        nbytes, changed = _write_json(WEB_DATA / name, payload)
        n_players = len(payload)
        n_rows = sum(len(v) for v in payload.values())
        n_explain = sum(1 for rows in payload.values() for r in rows if "explain" in r)
        flag = "" if changed else "  unchanged"
        print(
            f"  {name:28} {nbytes:8,} bytes  {n_players:4} players  {n_rows:6} rows  "
            f"explain={n_explain}{flag}",
            flush=True,
        )
        if nbytes > 25 * 1024 * 1024:
            too_big.append((season, nbytes))

    if too_big:
        print("WARNING: matchlog files over ~25 MB:", flush=True)
        for season, nbytes in too_big:
            print(f"  matchlogs_{season}.json  {nbytes:,} bytes", flush=True)

    print("matchlog coverage", flush=True)
    for row in coverage:
        if row["season"] not in by_season:
            continue
        print(
            f"  {row['season']}  gws={row['gws']}  "
            f"blank_rows={row['blank_rows']}  "
            f"dgw_player_gws={row['dgw_player_gws']}  "
            f"sgw_missing_opp={row['sgw_missing_opp_code']}  "
            f"sgw_missing_score={row['sgw_missing_score']}  "
            f"missing_date={row['missing_date']}  "
            f"missing_fixture={row['missing_fixture']}",
            flush=True,
        )

    print("web/data pricehistory", flush=True)
    pricehistory_blobs: dict[str, bytes] = {}
    pricehistory_total = 0
    for season in sorted(histories.keys(), reverse=True):
        blob = json.dumps(
            histories[season],
            ensure_ascii=False,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
        pricehistory_blobs[season] = blob
        pricehistory_total += len(blob)
        print(
            f"  {'pricehistory_' + season + '.json':28} {len(blob):8,} bytes  "
            f"{len(histories[season]):4} players",
            flush=True,
        )
    print(f"  pricehistory total {pricehistory_total:,} bytes", flush=True)
    if pricehistory_total > 25 * 1024 * 1024:
        print(
            "STOP: pricehistory_*.json total exceeds 25 MB. "
            "Not writing those files. Options: weekly ownership for completed "
            "seasons, or one season per commit.",
            flush=True,
        )
        raise SystemExit(1)
    for season, blob in pricehistory_blobs.items():
        name = f"pricehistory_{season}.json"
        keep.add(name)
        path = WEB_DATA / name
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.read_bytes() == blob:
            print(f"  {name:28} unchanged", flush=True)
            continue
        path.write_bytes(blob)

    print("web/data clusters", flush=True)
    export_style_json(keep)

    print("web/data metric register", flush=True)
    from transform.metric_register import (
        build_metric_register,
        print_tier_counts,
        validate_metric_register,
        write_metric_register,
        write_metrics_register_json,
    )

    register = build_metric_register()
    write_metric_register(register)
    write_metrics_register_json(register, WEB_DATA / "metrics_register.json")
    keep.add("metrics_register.json")
    print_tier_counts(register)
    reg_errs = validate_metric_register(register, WEB_DATA)
    if reg_errs:
        print("metric register checks:", flush=True)
        for err in reg_errs:
            print(f"  {err}", flush=True)
        raise RuntimeError("metric register validation failed:\n- " + "\n- ".join(reg_errs))
    print("metric register validation passed", flush=True)

    manifest = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "seasons": season_meta,
    }
    nbytes, changed = _write_json(WEB_DATA / "manifest.json", manifest)
    flag = "" if changed else "  unchanged"
    print(f"  {'manifest.json':28} {nbytes:8,} bytes{flag}", flush=True)

    for path in WEB_DATA.glob("*.json"):
        if path.name not in keep:
            path.unlink()
            print(f"  removed {path.name}", flush=True)

    return {"manifest": manifest}


def main() -> None:
    export()


if __name__ == "__main__":
    try:
        main()
    except FileNotFoundError as exc:
        print(exc, file=sys.stderr)
        raise SystemExit(1) from exc
