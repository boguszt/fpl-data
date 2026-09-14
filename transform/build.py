from __future__ import annotations

import shutil
from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd

from ingest.client import season_from_bootstrap
from ingest.paths import DB_DIR, DB_PATH, MARTS
from transform.columns import (
    FACT_PLAYER_FIXTURE_COLS,
    FACT_PLAYER_GW_COLS,
    INT_COLS,
    LIVE_STATS_MAP,
    SUM_STAT_COLS,
)
from transform.fplcache import (
    fplcache_dir,
    inspect_fplcache,
    load_fplcache_rows,
    print_inspect,
    rows_from_payload,
)
from transform.load import (
    latest_bootstrap,
    load_bootstrap_snapshots,
    load_history_past,
    load_latest_api_fixtures,
    load_master_team_list,
    load_merged_gw,
    load_metric_direction,
    load_players_raw,
    load_region_lookup,
    load_style_features,
    load_teams_csv,
    load_vaastav_fixtures,
    load_live_payloads,
)
from transform.validate import validate_marts
from transform.derived import build_derived_tables, season_element_type
from transform.opta import build_fact_player_season_opta, print_coverage
from transform.style import (
    CHOSEN_K,
    build_cluster_tables,
    build_fact_player_style,
    print_style_coverage,
)

FLOAT_COLS = [
    "influence",
    "creativity",
    "threat",
    "ict_index",
    "expected_goals",
    "expected_assists",
    "expected_goal_involvements",
    "expected_goals_conceded",
    "xp",
    "selected_by_percent",
]


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def _to_int(s: pd.Series) -> pd.Series:
    return _num(s).astype("Int64")


def _opta_int(s: pd.Series) -> pd.Series:
    cleaned = s.astype("string").str.strip().str.replace(r"^[pP]", "", regex=True)
    return pd.to_numeric(cleaned, errors="coerce").astype("Int64")


def build_id_maps(
    players_raw: pd.DataFrame, master: pd.DataFrame, teams_csv: pd.DataFrame, bootstrap: dict
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """dim_season_map, dim_team, name_to_code, team_id_to_code."""
    pr = players_raw.copy()
    pr["element_id"] = _to_int(pr["id"])
    pr["player_code"] = _to_int(pr["code"])
    pr["team_id"] = _to_int(pr["team"])
    pr["team_code"] = _to_int(pr["team_code"])

    dim_season_map = (
        pr[["season", "element_id", "player_code", "team_id", "team_code"]]
        .drop_duplicates(subset=["season", "element_id"], keep="last")
        .reset_index(drop=True)
    )

    current_season = season_from_bootstrap(bootstrap)
    boot_map = pd.DataFrame(
        [
            {
                "season": current_season,
                "element_id": el["id"],
                "player_code": el["code"],
                "team_id": el["team"],
                "team_code": el["team_code"],
            }
            for el in bootstrap.get("elements") or []
        ]
    )
    if not boot_map.empty:
        dim_season_map = (
            pd.concat([dim_season_map, boot_map], ignore_index=True)
            .drop_duplicates(subset=["season", "element_id"], keep="last")
            .reset_index(drop=True)
        )

    master = master.rename(columns={"team": "team_id", "team_name": "team_name"})
    master["team_id"] = _to_int(master["team_id"])

    from_pr = (
        pr[["season", "team_id", "team_code"]]
        .drop_duplicates()
        .merge(master, on=["season", "team_id"], how="left")
    )
    from_pr["name"] = from_pr["team_name"]
    from_pr["short_name"] = pd.NA
    from_pr["code"] = from_pr["team_code"]
    from_pr["id"] = from_pr["team_id"]

    frames = [from_pr[["season", "code", "id", "name", "short_name"]]]
    if not teams_csv.empty:
        tc = teams_csv.copy()
        tc["code"] = _to_int(tc["code"])
        tc["id"] = _to_int(tc["id"])
        frames.append(tc[["season", "code", "id", "name", "short_name"]])

    boot_teams = pd.DataFrame(
        [
            {
                "season": current_season,
                "code": t["code"],
                "id": t["id"],
                "name": t["name"],
                "short_name": t.get("short_name"),
            }
            for t in bootstrap.get("teams") or []
        ]
    )
    if not boot_teams.empty:
        frames.append(boot_teams)

    dim_team = (
        pd.concat(frames, ignore_index=True, sort=False)
        .dropna(subset=["season", "code"])
        .drop_duplicates(subset=["season", "code"], keep="last")
        .reset_index(drop=True)
    )
    dim_team["code"] = _to_int(dim_team["code"])
    dim_team["id"] = _to_int(dim_team["id"])

    name_to_code = (
        from_pr.dropna(subset=["team_name", "team_code"])[["season", "team_name", "team_code"]]
        .drop_duplicates()
        .rename(columns={"team_name": "team"})
    )
    # Also map official FPL names (teams.csv / bootstrap) in case they differ.
    extra_names = dim_team.dropna(subset=["name", "code"])[["season", "name", "code"]].rename(
        columns={"name": "team", "code": "team_code"}
    )
    name_to_code = pd.concat([name_to_code, extra_names], ignore_index=True).drop_duplicates(
        subset=["season", "team"], keep="last"
    )

    team_id_to_code = dim_team.dropna(subset=["id", "code"])[["season", "id", "code"]].rename(
        columns={"id": "team_id", "code": "team_code"}
    ).drop_duplicates(subset=["season", "team_id"], keep="last")

    return dim_season_map, dim_team, name_to_code, team_id_to_code


def build_dim_player(players_raw: pd.DataFrame, bootstrap: dict) -> pd.DataFrame:
    keep = [
        "season",
        "code",
        "first_name",
        "second_name",
        "web_name",
        "element_type",
        "team_code",
    ]
    optional = ["opta_code", "birth_date", "region"]
    cols = [c for c in keep + optional if c in players_raw.columns]
    hist = players_raw[cols].copy()
    hist["code"] = _to_int(hist["code"])

    acc: pd.DataFrame | None = None
    for season, chunk in hist.groupby("season", sort=True):
        piece = chunk.drop(columns=["season"]).drop_duplicates(subset=["code"], keep="last")
        piece = piece.set_index("code")
        acc = piece if acc is None else piece.combine_first(acc)
    assert acc is not None
    dim = acc.reset_index().rename(
        columns={"code": "code", "element_type": "current_element_type", "team_code": "current_team_code"}
    )

    boot = pd.DataFrame(bootstrap.get("elements") or [])
    if not boot.empty:
        overlay = pd.DataFrame(
            {
                "code": _to_int(boot["code"]),
                "opta_code": boot["opta_code"] if "opta_code" in boot.columns else pd.NA,
                "first_name": boot["first_name"],
                "second_name": boot["second_name"],
                "web_name": boot["web_name"],
                "birth_date": boot["birth_date"] if "birth_date" in boot.columns else pd.NA,
                "region": boot["region"] if "region" in boot.columns else pd.NA,
                "current_element_type": boot["element_type"],
                "current_team_code": boot["team_code"],
            }
        ).drop_duplicates(subset=["code"], keep="last")
        dim = overlay.set_index("code").combine_first(dim.set_index("code")).reset_index()

    for col in (
        "code",
        "region",
        "current_element_type",
        "current_team_code",
    ):
        if col in dim.columns:
            dim[col] = _to_int(dim[col])
    if "opta_code" in dim.columns:
        dim["opta_code"] = _opta_int(dim["opta_code"])
    dim["opta_code"] = dim["opta_code"].fillna(dim["code"])
    ordered = [
        "code",
        "opta_code",
        "first_name",
        "second_name",
        "web_name",
        "birth_date",
        "region",
        "current_element_type",
        "current_team_code",
    ]
    for col in ordered:
        if col not in dim.columns:
            dim[col] = pd.NA
    return dim[ordered]


def _normalise_merged_gw(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw.copy()
    if "GW" in df.columns:
        df["gw"] = df["GW"]
    elif "round" in df.columns:
        df["gw"] = df["round"]
    rename = {
        "element": "element_id",
        "goals_scored": "goals",
        "yellow_cards": "yellow",
        "red_cards": "red",
        "xP": "xp",
        "opponent_team": "opponent_team_id",
    }
    df = df.rename(columns={k: v for k, v in rename.items() if k in df.columns})
    return df


def _sum_keep_na(s: pd.Series) -> float:
    return pd.to_numeric(s, errors="coerce").sum(min_count=1)


def _apps_from_minutes(minutes: pd.Series) -> tuple[pd.Series, pd.Series]:
    m = pd.to_numeric(minutes, errors="coerce").fillna(0)
    return (m > 0).astype("int64"), (m >= 60).astype("int64")


def _player_gw_appearances(rows: pd.DataFrame, keys: list[str]) -> pd.DataFrame:
    """Matches with minutes > 0 / >= 60, counting distinct fixtures not GW rows."""
    work = rows.dropna(subset=keys).copy()
    if work.empty:
        return pd.DataFrame(columns=keys + ["appearances", "appearances_60_plus"])
    if "fixture" in work.columns:
        work = work.drop_duplicates(keys + ["fixture"], keep="first")
    else:
        work = work.drop_duplicates(keys, keep="first")
    if "minutes" not in work.columns:
        work["appearances"] = 0
        work["appearances_60_plus"] = 0
        return work[keys + ["appearances", "appearances_60_plus"]]
    apps, apps60 = _apps_from_minutes(work["minutes"])
    work = work.assign(appearances=apps, appearances_60_plus=apps60)
    return (
        work.groupby(keys, dropna=False)[["appearances", "appearances_60_plus"]]
        .sum()
        .reset_index()
    )


def _attach_row_apps(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    if out.empty:
        out["appearances"] = pd.Series(dtype="int64")
        out["appearances_60_plus"] = pd.Series(dtype="int64")
        return out
    if "minutes" not in out.columns:
        out["appearances"] = 0
        out["appearances_60_plus"] = 0
        return out
    apps, apps60 = _apps_from_minutes(out["minutes"])
    out["appearances"] = apps
    out["appearances_60_plus"] = apps60
    return out


def _explain_apps(explain, fallback_minutes) -> tuple[int, int]:
    seen: set = set()
    mins: list[int] = []
    for block in explain or []:
        fid = block.get("fixture")
        if fid is not None:
            if fid in seen:
                continue
            seen.add(fid)
        value = 0
        for stat in block.get("stats") or []:
            if stat.get("identifier") == "minutes":
                try:
                    value = int(stat.get("value") or 0)
                except (TypeError, ValueError):
                    value = 0
                break
        mins.append(value)
    if mins:
        return sum(m > 0 for m in mins), sum(m >= 60 for m in mins)
    try:
        m = int(fallback_minutes or 0)
    except (TypeError, ValueError):
        m = 0
    return (1 if m > 0 else 0), (1 if m >= 60 else 0)


def _collapse_dgw(df: pd.DataFrame) -> pd.DataFrame:
    """One row per (season, gw, player_code).

    Vaastav sometimes repeats the same fixture (keep first). True double-GWs
    have multiple fixture ids: sum stats and null opponent / was_home.

    Appearances are counted from distinct fixtures with minutes > 0, not from
    the collapsed GW row. A DGW of 90 and 75 is two appearances and two 60+.
    """
    keys = ["season", "gw", "player_code"]
    unmapped = df[df["player_code"].isna()].copy()
    work = df.dropna(subset=["player_code"]).copy()
    if work.empty:
        return _attach_row_apps(df) if "minutes" in df.columns else df

    dup_mask = work.duplicated(keys, keep=False)
    single = work.loc[~dup_mask].copy()
    multi = work.loc[dup_mask].copy()

    pieces = [single]
    if not multi.empty:
        if "fixture" in multi.columns:
            nfix = multi.groupby(keys)["fixture"].transform("nunique")
            source_dupes = multi.loc[nfix.fillna(1) <= 1]
            dgw = multi.loc[nfix.fillna(1) > 1]
        else:
            source_dupes = multi
            dgw = multi.iloc[0:0].copy()

        if not source_dupes.empty:
            pieces.append(source_dupes.drop_duplicates(keys, keep="first"))

        if not dgw.empty:
            agg: dict[str, object] = {}
            for col in SUM_STAT_COLS:
                if col in dgw.columns:
                    agg[col] = _sum_keep_na
            for col in ("team_code", "value", "xp", "element_id"):
                if col in dgw.columns:
                    agg[col] = "first"
            collapsed = dgw.groupby(keys, dropna=False).agg(agg).reset_index()
            collapsed["was_home"] = pd.NA
            collapsed["opponent"] = pd.NA
            pieces.append(collapsed)

    out = pd.concat(pieces, ignore_index=True, sort=False)
    apps = _player_gw_appearances(work, keys)
    out = out.drop(columns=["appearances", "appearances_60_plus"], errors="ignore")
    out = out.merge(apps, on=keys, how="left")
    out["appearances"] = out["appearances"].fillna(0).astype("int64")
    out["appearances_60_plus"] = out["appearances_60_plus"].fillna(0).astype("int64")
    if "fixture" in out.columns:
        out = out.drop(columns=["fixture"])
    if unmapped.empty:
        return out
    unmapped = _attach_row_apps(unmapped)
    return pd.concat([out, unmapped], ignore_index=True, sort=False)


def _map_vaastav_player_rows(
    merged: pd.DataFrame,
    dim_season_map: pd.DataFrame,
    name_to_code: pd.DataFrame,
    team_id_to_code: pd.DataFrame,
) -> pd.DataFrame:
    """Fixture-level mapped vaastav rows. Still has `fixture`; not collapsed."""
    df = _normalise_merged_gw(merged)
    df["element_id"] = _to_int(df["element_id"])
    df["gw"] = _to_int(df["gw"])
    if "fixture" in df.columns:
        df["fixture"] = _to_int(df["fixture"])
    for col in SUM_STAT_COLS + ["value", "xp"]:
        if col in df.columns:
            df[col] = _num(df[col])
    df = df.merge(
        dim_season_map[["season", "element_id", "player_code"]],
        on=["season", "element_id"],
        how="left",
    )
    if "team" in df.columns:
        df = df.merge(name_to_code, on=["season", "team"], how="left")
    if "opponent_team_id" in df.columns:
        df["opponent_team_id"] = _to_int(df["opponent_team_id"])
        df = df.merge(
            team_id_to_code.rename(
                columns={"team_code": "opponent", "team_id": "opponent_team_id"}
            ),
            on=["season", "opponent_team_id"],
            how="left",
        )
    elif "opponent" not in df.columns:
        df["opponent"] = pd.NA
    return df


def vaastav_player_gw(
    merged: pd.DataFrame,
    dim_season_map: pd.DataFrame,
    name_to_code: pd.DataFrame,
    team_id_to_code: pd.DataFrame,
) -> pd.DataFrame:
    df = _map_vaastav_player_rows(merged, dim_season_map, name_to_code, team_id_to_code)
    return coerce_fact_player_gw(_collapse_dgw(df))


def _player_fixture_from_mapped(mapped: pd.DataFrame) -> pd.DataFrame:
    work = mapped.dropna(subset=["player_code"]).copy()
    if work.empty:
        return coerce_fact_player_fixture(work)
    keys = ["season", "gw", "player_code"]
    if "fixture" in work.columns:
        work = work.drop_duplicates(keys + ["fixture"], keep="first")
        work = work.rename(columns={"fixture": "fixture_id"})
    else:
        work = work.drop_duplicates(keys, keep="first")
        work["fixture_id"] = pd.NA
    return coerce_fact_player_fixture(work)


def coerce_fact_player_fixture(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in FACT_PLAYER_FIXTURE_COLS:
        if col not in out.columns:
            out[col] = pd.NA
    out = out[FACT_PLAYER_FIXTURE_COLS]
    out["season"] = out["season"].astype(str)
    for col in INT_COLS:
        if col in out.columns:
            out[col] = _to_int(out[col])
    out["fixture_id"] = _to_int(out["fixture_id"])
    for col in FLOAT_COLS:
        if col in out.columns:
            out[col] = _num(out[col])
    out["was_home"] = out["was_home"].map(
        lambda v: pd.NA if pd.isna(v) else bool(v)
    ).astype("boolean")
    if "value_source" in out.columns:
        out["value_source"] = out["value_source"].astype("string")
    return out


def coerce_fact_player_gw(df: pd.DataFrame) -> pd.DataFrame:
    out = df.copy()
    for col in FACT_PLAYER_GW_COLS:
        if col not in out.columns:
            out[col] = pd.NA
    out = out[FACT_PLAYER_GW_COLS]
    out["season"] = out["season"].astype(str)
    for col in INT_COLS:
        out[col] = _to_int(out[col])
    for col in FLOAT_COLS:
        if col in out.columns:
            out[col] = _num(out[col])
    out["was_home"] = out["was_home"].map(
        lambda v: pd.NA if pd.isna(v) else bool(v)
    ).astype("boolean")
    if "value_source" in out.columns:
        out["value_source"] = out["value_source"].astype("string")
    return out


def _snapshot_elements(snaps: list[tuple[datetime, dict]]) -> pd.DataFrame:
    rows: list[dict] = []
    for ts, payload in snaps:
        season = season_from_bootstrap(payload)
        for el in payload.get("elements") or []:
            rows.append(
                {
                    "snapshot_ts": ts,
                    "season": season,
                    "element_id": el["id"],
                    "player_code": el["code"],
                    "team_code": el.get("team_code"),
                    "now_cost": el.get("now_cost"),
                }
            )
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["element_id"] = _to_int(df["element_id"])
    df["player_code"] = _to_int(df["player_code"])
    df["team_code"] = _to_int(df["team_code"])
    df["now_cost"] = _to_int(df["now_cost"])
    return df


def _gw_deadlines(bootstrap: dict, season: str) -> pd.DataFrame:
    rows = []
    for ev in bootstrap.get("events") or []:
        raw = str(ev.get("deadline_time") or "").replace("Z", "+00:00")
        if not raw:
            continue
        rows.append(
            {
                "season": season,
                "gw": int(ev["id"]),
                "deadline_time": datetime.fromisoformat(raw),
            }
        )
    return pd.DataFrame(rows)


def _asof_snapshot_pre_deadline(
    keys: pd.DataFrame, snap_el: pd.DataFrame, deadlines: pd.DataFrame
) -> pd.DataFrame:
    """Latest snapshot at or before each GW deadline, per player."""
    empty = pd.DataFrame(columns=["season", "gw", "player_code", "now_cost"])
    if keys.empty or snap_el.empty or deadlines.empty:
        return empty
    keyed = keys.merge(deadlines, on=["season", "gw"], how="left")
    keyed = keyed.dropna(subset=["deadline_time", "player_code"])
    if keyed.empty:
        return empty
    left = keyed.copy()
    left["player_code"] = left["player_code"].astype("int64")
    left["gw"] = left["gw"].astype("int64")
    right = snap_el.dropna(subset=["player_code", "snapshot_ts"]).copy()
    right["player_code"] = right["player_code"].astype("int64")
    merged = pd.merge_asof(
        left.sort_values("deadline_time"),
        right.sort_values("snapshot_ts"),
        left_on="deadline_time",
        right_on="snapshot_ts",
        by=["season", "player_code"],
        direction="backward",
    )
    return merged[["season", "gw", "player_code", "now_cost"]]


def live_player_gw(
    dim_season_map: pd.DataFrame,
    vaastav: pd.DataFrame,
    team_id_to_code: pd.DataFrame,
    api_fixtures: dict[str, list],
    snaps: list[tuple[datetime, dict]],
    latest_boot: dict,
) -> pd.DataFrame:
    payloads = load_live_payloads()
    if not payloads:
        return pd.DataFrame(columns=FACT_PLAYER_GW_COLS)

    frames: list[pd.DataFrame] = []
    for season, gw, payload in payloads:
        rows = []
        for el in payload.get("elements") or []:
            stats = el.get("stats") or {}
            explain = el.get("explain") or []
            fids = [x.get("fixture") for x in explain if x.get("fixture") is not None]
            row: dict = {"season": season, "gw": gw, "element_id": el["id"]}
            for src, dest in LIVE_STATS_MAP.items():
                row[dest] = stats.get(src)
            apps, apps60 = _explain_apps(explain, stats.get("minutes"))
            row["appearances"] = apps
            row["appearances_60_plus"] = apps60
            row["n_fixtures"] = len(fids)
            row["fixture_id"] = fids[0] if len(fids) == 1 else pd.NA
            rows.append(row)
        frames.append(pd.DataFrame(rows))
    live = pd.concat(frames, ignore_index=True)
    live["element_id"] = _to_int(live["element_id"])
    live["gw"] = _to_int(live["gw"])
    live = live.merge(
        dim_season_map[["season", "element_id", "player_code"]],
        on=["season", "element_id"],
        how="left",
    )

    ctx_cols = ["season", "gw", "player_code", "team_code", "value", "xp", "opponent", "was_home"]
    ctx = vaastav[ctx_cols].rename(
        columns={
            "team_code": "team_code_vaastav",
            "value": "value_vaastav",
            "xp": "xp_vaastav",
            "opponent": "opponent_vaastav",
            "was_home": "was_home_vaastav",
        }
    )
    live = live.merge(ctx, on=["season", "gw", "player_code"], how="left")

    snap_el = _snapshot_elements(snaps)
    if not snap_el.empty:
        latest_team = (
            snap_el.sort_values("snapshot_ts")
            .groupby(["season", "player_code"], as_index=False)
            .tail(1)[["season", "player_code", "team_code"]]
            .rename(columns={"team_code": "team_code_snap"})
        )
        live = live.merge(latest_team, on=["season", "player_code"], how="left")
    else:
        live["team_code_snap"] = pd.NA

    live["team_code"] = _to_int(live["team_code_vaastav"]).fillna(_to_int(live["team_code_snap"]))
    live["value"] = pd.NA
    live["xp"] = pd.NA
    live["value_source"] = pd.NA

    fx_rows: list[dict] = []
    for season, payload in api_fixtures.items():
        for fx in payload or []:
            fx_rows.append(
                {
                    "season": season,
                    "fixture_id": fx.get("id"),
                    "home_team_id": fx.get("team_h"),
                    "away_team_id": fx.get("team_a"),
                }
            )
    if fx_rows:
        fx = pd.DataFrame(fx_rows)
        fx["fixture_id"] = _to_int(fx["fixture_id"])
        fx["home_team_id"] = _to_int(fx["home_team_id"])
        fx["away_team_id"] = _to_int(fx["away_team_id"])
        fx = fx.merge(
            team_id_to_code.rename(columns={"team_id": "home_team_id", "team_code": "home_team_code"}),
            on=["season", "home_team_id"],
            how="left",
        )
        fx = fx.merge(
            team_id_to_code.rename(columns={"team_id": "away_team_id", "team_code": "away_team_code"}),
            on=["season", "away_team_id"],
            how="left",
        )
        live["fixture_id"] = _to_int(live["fixture_id"])
        live = live.merge(
            fx[["season", "fixture_id", "home_team_code", "away_team_code"]],
            on=["season", "fixture_id"],
            how="left",
        )
        home_match = live["team_code"].notna() & (live["team_code"] == live["home_team_code"])
        away_match = live["team_code"].notna() & (live["team_code"] == live["away_team_code"])
        from_fx_home = home_match.fillna(False)
        from_fx_away = away_match.fillna(False)
        live["was_home"] = pd.Series(pd.NA, index=live.index, dtype="boolean")
        live.loc[from_fx_home, "was_home"] = True
        live.loc[from_fx_away, "was_home"] = False
        live["opponent"] = pd.Series(pd.NA, index=live.index, dtype="Int64")
        live.loc[from_fx_home, "opponent"] = live.loc[from_fx_home, "away_team_code"]
        live.loc[from_fx_away, "opponent"] = live.loc[from_fx_away, "home_team_code"]
        unmatched = ~(from_fx_home | from_fx_away)
        live.loc[unmatched, "was_home"] = live.loc[unmatched, "was_home_vaastav"]
        live.loc[unmatched, "opponent"] = live.loc[unmatched, "opponent_vaastav"]
    else:
        live["was_home"] = live["was_home_vaastav"]
        live["opponent"] = live["opponent_vaastav"]

    multi = live["n_fixtures"].fillna(0) != 1
    live.loc[multi, "was_home"] = pd.NA
    live.loc[multi, "opponent"] = pd.NA

    return coerce_fact_player_gw(live)


def _stats_from_explain_block(block: dict) -> dict:
    out: dict = {}
    points = 0
    for stat in block.get("stats") or []:
        ident = stat.get("identifier")
        dest = LIVE_STATS_MAP.get(ident)
        if dest:
            out[dest] = stat.get("value")
        try:
            points += int(stat.get("points") or 0)
        except (TypeError, ValueError):
            pass
    out["total_points"] = points
    return out


def _api_fixture_sides(
    api_fixtures: dict[str, list], team_id_to_code: pd.DataFrame
) -> pd.DataFrame:
    fx_rows: list[dict] = []
    for season, payload in api_fixtures.items():
        for fx in payload or []:
            fx_rows.append(
                {
                    "season": season,
                    "gw": fx.get("event"),
                    "fixture_id": fx.get("id"),
                    "home_team_id": fx.get("team_h"),
                    "away_team_id": fx.get("team_a"),
                }
            )
    if not fx_rows:
        return pd.DataFrame()
    fx = pd.DataFrame(fx_rows)
    fx["fixture_id"] = _to_int(fx["fixture_id"])
    fx["gw"] = _to_int(fx["gw"])
    fx["home_team_id"] = _to_int(fx["home_team_id"])
    fx["away_team_id"] = _to_int(fx["away_team_id"])
    fx = fx.merge(
        team_id_to_code.rename(
            columns={"team_id": "home_team_id", "team_code": "home_team_code"}
        ),
        on=["season", "home_team_id"],
        how="left",
    )
    fx = fx.merge(
        team_id_to_code.rename(
            columns={"team_id": "away_team_id", "team_code": "away_team_code"}
        ),
        on=["season", "away_team_id"],
        how="left",
    )
    return fx[["season", "gw", "fixture_id", "home_team_code", "away_team_code"]]


def _fill_singleton_fixture_id(live: pd.DataFrame, fx: pd.DataFrame) -> pd.DataFrame:
    if live.empty or fx.empty:
        return live
    stacked = pd.concat(
        [
            fx[["season", "gw", "fixture_id"]].assign(team_code=fx["home_team_code"]),
            fx[["season", "gw", "fixture_id"]].assign(team_code=fx["away_team_code"]),
        ],
        ignore_index=True,
    )
    stacked = stacked.dropna(subset=["team_code", "fixture_id"])
    nfix = stacked.groupby(["season", "gw", "team_code"], dropna=False)["fixture_id"].transform(
        "nunique"
    )
    singles = stacked.loc[nfix == 1, ["season", "gw", "team_code", "fixture_id"]].drop_duplicates()
    singles = singles.rename(columns={"fixture_id": "fixture_id_fill"})
    out = live.merge(singles, on=["season", "gw", "team_code"], how="left")
    missing = out["fixture_id"].isna()
    out.loc[missing, "fixture_id"] = out.loc[missing, "fixture_id_fill"]
    return out.drop(columns=["fixture_id_fill"])


def live_player_fixture(
    dim_season_map: pd.DataFrame,
    vaastav: pd.DataFrame,
    team_id_to_code: pd.DataFrame,
    api_fixtures: dict[str, list],
    snaps: list[tuple[datetime, dict]],
) -> pd.DataFrame:
    """One live row per explain[] fixture. SGW uses the full stats blob."""
    payloads = load_live_payloads()
    if not payloads:
        return pd.DataFrame(columns=FACT_PLAYER_FIXTURE_COLS)

    frames: list[pd.DataFrame] = []
    for season, gw, payload in payloads:
        rows = []
        for el in payload.get("elements") or []:
            stats = el.get("stats") or {}
            explain = el.get("explain") or []
            fids: list = []
            blocks: dict = {}
            for block in explain:
                fid = block.get("fixture")
                if fid is None or fid in blocks:
                    continue
                blocks[fid] = block
                fids.append(fid)
            if len(fids) <= 1:
                row: dict = {
                    "season": season,
                    "gw": gw,
                    "element_id": el["id"],
                    "fixture_id": fids[0] if fids else pd.NA,
                }
                for src, dest in LIVE_STATS_MAP.items():
                    row[dest] = stats.get(src)
                rows.append(row)
            else:
                for fid in fids:
                    mapped = _stats_from_explain_block(blocks[fid])
                    row = {
                        "season": season,
                        "gw": gw,
                        "element_id": el["id"],
                        "fixture_id": fid,
                    }
                    for dest in LIVE_STATS_MAP.values():
                        row[dest] = mapped.get(dest, pd.NA)
                    rows.append(row)
        if rows:
            frames.append(pd.DataFrame(rows))
    if not frames:
        return pd.DataFrame(columns=FACT_PLAYER_FIXTURE_COLS)

    live = pd.concat(frames, ignore_index=True)
    live["element_id"] = _to_int(live["element_id"])
    live["gw"] = _to_int(live["gw"])
    live["fixture_id"] = _to_int(live["fixture_id"])
    live = live.merge(
        dim_season_map[["season", "element_id", "player_code"]],
        on=["season", "element_id"],
        how="left",
    )

    ctx_cols = ["season", "gw", "player_code", "team_code", "value", "xp"]
    ctx = vaastav[ctx_cols].drop_duplicates(subset=["season", "gw", "player_code"])
    ctx = ctx.rename(columns={"team_code": "team_code_vaastav"})
    live = live.merge(ctx, on=["season", "gw", "player_code"], how="left")

    snap_el = _snapshot_elements(snaps)
    if not snap_el.empty:
        latest_team = (
            snap_el.sort_values("snapshot_ts")
            .groupby(["season", "player_code"], as_index=False)
            .tail(1)[["season", "player_code", "team_code"]]
            .rename(columns={"team_code": "team_code_snap"})
        )
        live = live.merge(latest_team, on=["season", "player_code"], how="left")
    else:
        live["team_code_snap"] = pd.NA

    live["team_code"] = _to_int(live["team_code_vaastav"]).fillna(_to_int(live["team_code_snap"]))
    if "value" not in live.columns:
        live["value"] = pd.NA
    live["value_source"] = pd.NA
    if "xp" not in live.columns:
        live["xp"] = pd.NA

    fx = _api_fixture_sides(api_fixtures, team_id_to_code)
    live = _fill_singleton_fixture_id(live, fx)
    if not fx.empty:
        live = live.merge(
            fx[["season", "fixture_id", "home_team_code", "away_team_code"]],
            on=["season", "fixture_id"],
            how="left",
        )
        home_match = live["team_code"].notna() & (live["team_code"] == live["home_team_code"])
        away_match = live["team_code"].notna() & (live["team_code"] == live["away_team_code"])
        from_fx_home = home_match.fillna(False)
        from_fx_away = away_match.fillna(False)
        live["was_home"] = pd.Series(pd.NA, index=live.index, dtype="boolean")
        live.loc[from_fx_home, "was_home"] = True
        live.loc[from_fx_away, "was_home"] = False
        live["opponent"] = pd.Series(pd.NA, index=live.index, dtype="Int64")
        live.loc[from_fx_home, "opponent"] = live.loc[from_fx_home, "away_team_code"]
        live.loc[from_fx_away, "opponent"] = live.loc[from_fx_away, "home_team_code"]
    else:
        live["was_home"] = pd.NA
        live["opponent"] = pd.NA

    return coerce_fact_player_fixture(live)


def combine_player_gw(vaastav: pd.DataFrame, live: pd.DataFrame) -> pd.DataFrame:
    return _replace_live_gws(vaastav, live)


def combine_player_fixture(vaastav: pd.DataFrame, live: pd.DataFrame) -> pd.DataFrame:
    return _replace_live_gws(vaastav, live)


def _replace_live_gws(hist: pd.DataFrame, live: pd.DataFrame) -> pd.DataFrame:
    if live.empty:
        return hist
    live_keys = live[["season", "gw"]].drop_duplicates()
    kept = hist.merge(live_keys, on=["season", "gw"], how="left", indicator=True)
    kept = kept.loc[kept["_merge"] == "left_only"].drop(columns=["_merge"])
    return pd.concat([kept, live], ignore_index=True)


def assign_value_and_xp(
    fact: pd.DataFrame,
    vaastav_ctx: pd.DataFrame,
    snaps: list[tuple[datetime, dict]],
    bootstrap: dict,
) -> pd.DataFrame:
    """Current season: pre-deadline snapshot first, then vaastav. Past: vaastav only.

    Snapshots taken after the deadline are never used for value. xp is vaastav-only.
    """
    current = season_from_bootstrap(bootstrap)
    out = fact.drop(columns=["value", "xp", "value_source"], errors="ignore").copy()
    ctx = vaastav_ctx.rename(columns={"value": "value_vaastav", "xp": "xp_vaastav"})
    out = out.merge(ctx, on=["season", "gw", "player_code"], how="left")

    snap_el = _snapshot_elements(snaps)
    deadlines = _gw_deadlines(bootstrap, current)
    keys = out.loc[out["season"] == current, ["season", "gw", "player_code"]].drop_duplicates()
    asof = _asof_snapshot_pre_deadline(keys, snap_el, deadlines)
    if asof.empty:
        out["now_cost"] = pd.NA
    else:
        asof = asof.rename(columns={"now_cost": "now_cost"})
        out = out.merge(asof, on=["season", "gw", "player_code"], how="left")

    out["value"] = pd.Series(pd.NA, index=out.index, dtype="Int64")
    out["value_source"] = pd.Series(pd.NA, index=out.index, dtype="object")
    out["xp"] = _num(out.get("xp_vaastav"))

    is_current = out["season"] == current
    if "now_cost" in out.columns:
        snap_ok = is_current & out["now_cost"].notna()
        out.loc[snap_ok, "value"] = _to_int(out.loc[snap_ok, "now_cost"])
        out.loc[snap_ok, "value_source"] = "snapshot"

    v_ok = out["value"].isna() & out["value_vaastav"].notna()
    out.loc[v_ok, "value"] = _to_int(out.loc[v_ok, "value_vaastav"])
    out.loc[v_ok, "value_source"] = "vaastav"
    return coerce_fact_player_gw(out)


def build_fact_fixture(
    vaastav_fx: pd.DataFrame,
    api_fixtures: dict[str, list],
    team_id_to_code: pd.DataFrame,
    merged_gw: pd.DataFrame,
    name_to_code: pd.DataFrame,
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    api_seasons = set()
    for season, payload in api_fixtures.items():
        api_seasons.add(season)
        rows = []
        for fx in payload or []:
            hs, aws = fx.get("team_h_score"), fx.get("team_a_score")
            result = None if hs is None or aws is None else f"{hs}-{aws}"
            rows.append(
                {
                    "season": season,
                    "gw": fx.get("event"),
                    "fixture_id": fx.get("id"),
                    "home_team_id": fx.get("team_h"),
                    "away_team_id": fx.get("team_a"),
                    "kickoff": fx.get("kickoff_time"),
                    "result": result,
                    "home_difficulty": fx.get("team_h_difficulty"),
                    "away_difficulty": fx.get("team_a_difficulty"),
                }
            )
        if rows:
            frames.append(pd.DataFrame(rows))

    if not vaastav_fx.empty:
        vf = vaastav_fx.copy()
        vf = vf[~vf["season"].isin(api_seasons)]
        if not vf.empty:
            hs, aws = vf.get("team_h_score"), vf.get("team_a_score")
            vf = vf.rename(
                columns={
                    "id": "fixture_id",
                    "event": "gw",
                    "team_h": "home_team_id",
                    "team_a": "away_team_id",
                    "kickoff_time": "kickoff",
                    "team_h_difficulty": "home_difficulty",
                    "team_a_difficulty": "away_difficulty",
                }
            )
            if hs is not None and aws is not None:
                vf["result"] = [
                    None if pd.isna(h) or pd.isna(a) else f"{int(h)}-{int(a)}"
                    for h, a in zip(vf["team_h_score"], vf["team_a_score"])
                ]
            keep = [
                "season",
                "gw",
                "fixture_id",
                "home_team_id",
                "away_team_id",
                "kickoff",
                "result",
                "home_difficulty",
                "away_difficulty",
            ]
            for c in keep:
                if c not in vf.columns:
                    vf[c] = pd.NA
            frames.append(vf[keep])

    covered = set()
    if frames:
        covered = set(pd.concat(frames, ignore_index=True)["season"].unique())

    # Skeleton from merged_gw for seasons with no fixtures file (e.g. 2016-17).
    mgw = _normalise_merged_gw(merged_gw)
    if "fixture" in mgw.columns:
        need = mgw[~mgw["season"].isin(covered)].copy()
        if not need.empty and "team" in need.columns:
            need = need.merge(name_to_code, on=["season", "team"], how="left")
            if "opponent_team_id" in need.columns:
                need["opponent_team_id"] = _to_int(need["opponent_team_id"])
                need = need.merge(
                    team_id_to_code.rename(
                        columns={"team_id": "opponent_team_id", "team_code": "opp_code"}
                    ),
                    on=["season", "opponent_team_id"],
                    how="left",
                )
            was_home = need["was_home"].astype("boolean") if "was_home" in need.columns else False
            need["home_team_code"] = pd.NA
            need["away_team_code"] = pd.NA
            need.loc[was_home.fillna(False), "home_team_code"] = need.loc[was_home.fillna(False), "team_code"]
            need.loc[was_home.fillna(False), "away_team_code"] = need.loc[was_home.fillna(False), "opp_code"]
            away = ~was_home.fillna(False)
            need.loc[away, "away_team_code"] = need.loc[away, "team_code"]
            need.loc[away, "home_team_code"] = need.loc[away, "opp_code"]
            hs = need["team_h_score"] if "team_h_score" in need.columns else None
            derived = need.groupby(["season", "fixture"], dropna=False).agg(
                gw=("gw", "first"),
                kickoff=("kickoff_time", "first") if "kickoff_time" in need.columns else ("fixture", "first"),
                home_team_code=("home_team_code", "first"),
                away_team_code=("away_team_code", "first"),
                team_h_score=("team_h_score", "first") if "team_h_score" in need.columns else ("fixture", "first"),
                team_a_score=("team_a_score", "first") if "team_a_score" in need.columns else ("fixture", "first"),
            ).reset_index().rename(columns={"fixture": "fixture_id"})
            derived["home_difficulty"] = pd.NA
            derived["away_difficulty"] = pd.NA
            derived["result"] = [
                None if pd.isna(h) or pd.isna(a) else f"{int(h)}-{int(a)}"
                for h, a in zip(derived["team_h_score"], derived["team_a_score"])
            ]
            derived["home_team_id"] = pd.NA
            derived["away_team_id"] = pd.NA
            frames.append(
                derived[
                    [
                        "season",
                        "gw",
                        "fixture_id",
                        "home_team_id",
                        "away_team_id",
                        "kickoff",
                        "result",
                        "home_difficulty",
                        "away_difficulty",
                        "home_team_code",
                        "away_team_code",
                    ]
                ]
            )

    if not frames:
        return pd.DataFrame(
            columns=[
                "season",
                "gw",
                "fixture_id",
                "home_team_code",
                "away_team_code",
                "kickoff",
                "result",
                "home_difficulty",
                "away_difficulty",
            ]
        )

    fx = pd.concat(frames, ignore_index=True, sort=False)
    if "home_team_code" not in fx.columns:
        fx["home_team_id"] = _to_int(fx["home_team_id"])
        fx["away_team_id"] = _to_int(fx["away_team_id"])
        fx = fx.merge(
            team_id_to_code.rename(columns={"team_id": "home_team_id", "team_code": "home_team_code"}),
            on=["season", "home_team_id"],
            how="left",
        )
        fx = fx.merge(
            team_id_to_code.rename(columns={"team_id": "away_team_id", "team_code": "away_team_code"}),
            on=["season", "away_team_id"],
            how="left",
        )
    else:
        # mixed frames: fill codes from ids where missing
        if "home_team_id" in fx.columns:
            fx["home_team_id"] = _to_int(fx["home_team_id"])
            fx["away_team_id"] = _to_int(fx["away_team_id"])
            missing = fx["home_team_code"].isna() & fx["home_team_id"].notna()
            if missing.any():
                mapped_h = fx.loc[missing, ["season", "home_team_id"]].merge(
                    team_id_to_code.rename(
                        columns={"team_id": "home_team_id", "team_code": "home_team_code_m"}
                    ),
                    on=["season", "home_team_id"],
                    how="left",
                )
                fx.loc[missing, "home_team_code"] = mapped_h["home_team_code_m"].to_numpy()
                mapped_a = fx.loc[missing, ["season", "away_team_id"]].merge(
                    team_id_to_code.rename(
                        columns={"team_id": "away_team_id", "team_code": "away_team_code_m"}
                    ),
                    on=["season", "away_team_id"],
                    how="left",
                )
                fx.loc[missing, "away_team_code"] = mapped_a["away_team_code_m"].to_numpy()

    out = pd.DataFrame(
        {
            "season": fx["season"].astype(str),
            "gw": _to_int(fx["gw"]),
            "fixture_id": _to_int(fx["fixture_id"]),
            "home_team_code": _to_int(fx["home_team_code"]),
            "away_team_code": _to_int(fx["away_team_code"]),
            "kickoff": fx["kickoff"],
            "result": fx["result"],
            "home_difficulty": _to_int(fx["home_difficulty"]) if "home_difficulty" in fx.columns else pd.NA,
            "away_difficulty": _to_int(fx["away_difficulty"]) if "away_difficulty" in fx.columns else pd.NA,
        }
    )
    return out.drop_duplicates(subset=["season", "fixture_id"], keep="last").reset_index(drop=True)


def build_snap_player_day(
    own_snaps: list[tuple[datetime, dict]], fplcache_rows: list[dict] | None = None
) -> pd.DataFrame:
    """Union own bootstrap snapshots with fplcache. Identity is player_code, never id."""
    rows: list[dict] = []
    for ts, payload in own_snaps:
        try:
            rows.extend(rows_from_payload(ts, payload, "own"))
        except ValueError as exc:
            print(f"  skip own snapshot {ts.isoformat()}: {exc}", flush=True)
    if fplcache_rows:
        rows.extend(fplcache_rows)
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df["player_code"] = _to_int(df["player_code"])
    df["now_cost"] = _to_int(df["now_cost"])
    df["transfers_in_event"] = _to_int(df["transfers_in_event"])
    df["transfers_out_event"] = _to_int(df["transfers_out_event"])
    df["chance_of_playing_next_round"] = _to_int(df["chance_of_playing_next_round"])
    df["selected_by_percent"] = _num(df["selected_by_percent"])
    df["source"] = df["source"].astype("string")
    df["season"] = df["season"].astype("string")
    df["status"] = df["status"].astype("string")
    df["news"] = df["news"].astype("string")
    df["snapshot_ts"] = df["snapshot_ts"].astype("string")
    return df.drop_duplicates(
        subset=["snapshot_ts", "player_code", "source"], keep="last"
    ).reset_index(drop=True)


def build_dim_setpieces(ts: datetime, bootstrap: dict) -> pd.DataFrame:
    rows = []
    as_of = ts.date().isoformat()
    for el in bootstrap.get("elements") or []:
        rows.append(
            {
                "player_code": el.get("code"),
                "penalties_order": el.get("penalties_order"),
                "direct_freekicks_order": el.get("direct_freekicks_order"),
                "corners_and_indirect_freekicks_order": el.get(
                    "corners_and_indirect_freekicks_order"
                ),
                "as_of": as_of,
            }
        )
    df = pd.DataFrame(rows)
    for col in (
        "player_code",
        "penalties_order",
        "direct_freekicks_order",
        "corners_and_indirect_freekicks_order",
    ):
        df[col] = _to_int(df[col])
    return df


def _write_outputs(tables: dict[str, pd.DataFrame], extra_checks: dict) -> None:
    staging = MARTS / ".staging"
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    MARTS.mkdir(parents=True, exist_ok=True)
    DB_DIR.mkdir(parents=True, exist_ok=True)

    db_staging = DB_DIR / "fpl.staging.duckdb"
    for leftover in (db_staging, Path(str(db_staging) + ".wal")):
        if leftover.exists():
            leftover.unlink()

    con = duckdb.connect(str(db_staging))
    try:
        for name, df in tables.items():
            con.register(f"_df_{name}", df)
            con.execute(f'CREATE TABLE "{name}" AS SELECT * FROM _df_{name}')
            dest = staging / f"{name}.parquet"
            con.execute(f"COPY {name} TO '{dest.as_posix()}' (FORMAT PARQUET)")
        current = extra_checks.get("current_players")
        if current is not None:
            con.register("_current_players", current)
            con.execute("CREATE TABLE current_players AS SELECT * FROM _current_players")
        validate_marts(con, extra_checks)
        con.execute("DROP TABLE IF EXISTS current_players")
    except Exception:
        con.close()
        raise

    con.close()
    for pq in staging.glob("*.parquet"):
        dest = MARTS / pq.name
        if dest.exists():
            dest.unlink()
        shutil.move(str(pq), str(dest))
    shutil.rmtree(staging)

    if DB_PATH.exists():
        DB_PATH.unlink()
    wal = Path(str(DB_PATH) + ".wal")
    if wal.exists():
        wal.unlink()
    db_staging.replace(DB_PATH)
    print(f"wrote marts to {MARTS} and {DB_PATH}")


def build_marts() -> None:
    print("loading raw...", flush=True)
    players_raw = load_players_raw()
    print(f"  players_raw {len(players_raw)}", flush=True)
    master = load_master_team_list()
    merged = load_merged_gw()
    print(f"  merged_gw {len(merged)}", flush=True)
    teams_csv = load_teams_csv()
    vaastav_fx = load_vaastav_fixtures()
    snaps = load_bootstrap_snapshots()
    ts, bootstrap = latest_bootstrap()
    api_fixtures = load_latest_api_fixtures()
    history_past = load_history_past()
    print(f"  history_past {len(history_past)}", flush=True)

    print("building dimensions...", flush=True)
    dim_season_map, dim_team, name_to_code, team_id_to_code = build_id_maps(
        players_raw, master, teams_csv, bootstrap
    )
    dim_player = build_dim_player(players_raw, bootstrap)
    dim_setpieces = build_dim_setpieces(ts, bootstrap)
    print(f"  dim_player {len(dim_player)} dim_team {len(dim_team)}", flush=True)

    print("building fact_player_gw...", flush=True)
    mapped = _map_vaastav_player_rows(merged, dim_season_map, name_to_code, team_id_to_code)
    vaastav_facts = coerce_fact_player_gw(_collapse_dgw(mapped))
    vaastav_fixture = _player_fixture_from_mapped(mapped)
    vaastav_ctx = vaastav_facts[["season", "gw", "player_code", "value", "xp"]].copy()
    live_facts = live_player_gw(
        dim_season_map,
        vaastav_facts,
        team_id_to_code,
        api_fixtures,
        snaps,
        bootstrap,
    )
    live_fixture = live_player_fixture(
        dim_season_map,
        vaastav_facts,
        team_id_to_code,
        api_fixtures,
        snaps,
    )
    fact_player_gw = combine_player_gw(vaastav_facts, live_facts)
    fact_player_gw = assign_value_and_xp(fact_player_gw, vaastav_ctx, snaps, bootstrap)
    print("building fact_player_fixture...", flush=True)
    fact_player_fixture = combine_player_fixture(vaastav_fixture, live_fixture)
    print(f"  fact_player_fixture {len(fact_player_fixture)}", flush=True)

    print("building remaining facts...")
    fact_fixture = build_fact_fixture(
        vaastav_fx, api_fixtures, team_id_to_code, merged, name_to_code
    )
    print("building snap_player_day...", flush=True)
    fpl_rows: list[dict] = []
    cache = fplcache_dir()
    if cache is None:
        print("  fplcache not found; own snapshots only", flush=True)
    else:
        info = inspect_fplcache(cache, measure_uncompressed=False)
        print_inspect(info)
        print("  streaming fplcache snapshots...", flush=True)
        fpl_rows, uncompressed = load_fplcache_rows(cache, info["files"])
        print(f"  fplcache rows {len(fpl_rows)}  uncompressed {uncompressed:,} bytes", flush=True)
    snap_player_day = build_snap_player_day(snaps, fpl_rows)
    print(f"  snap_player_day {len(snap_player_day)}", flush=True)
    fact_player_season = history_past.copy()
    if not fact_player_season.empty:
        fact_player_season["player_code"] = _to_int(fact_player_season["player_code"])

    print("building derived marts...", flush=True)
    metric_direction = load_metric_direction()
    region_lookup = load_region_lookup()
    derived = build_derived_tables(
        fact_player_gw,
        fact_fixture,
        dim_setpieces,
        players_raw,
        bootstrap,
        metric_direction,
        region_lookup,
    )
    extra: dict = {"current_players": derived.pop("_current_players")}

    print("building fact_player_season_opta...", flush=True)
    fact_player_season_opta, opta_coverage = build_fact_player_season_opta()
    print(f"  fact_player_season_opta {len(fact_player_season_opta)}", flush=True)
    print_coverage(opta_coverage)

    print("building fact_player_style...", flush=True)
    positions = season_element_type(players_raw, bootstrap)
    fact_player_style = build_fact_player_style(
        fact_player_season_opta,
        derived["fact_player_season_availability"],
        positions,
        load_style_features(),
    )
    print(f"  fact_player_style {len(fact_player_style)}", flush=True)
    print_style_coverage(fact_player_style)

    print(f"fitting style clusters k={CHOSEN_K}...", flush=True)
    dim_style_cluster, fact_player_cluster = build_cluster_tables(
        fact_player_style, load_style_features(), k=CHOSEN_K
    )
    print(f"  fact_player_cluster {len(fact_player_cluster)}", flush=True)

    tables = {
        "dim_player": dim_player,
        "dim_team": dim_team,
        "dim_season_map": dim_season_map,
        "fact_player_gw": fact_player_gw,
        "fact_player_fixture": fact_player_fixture,
        "fact_fixture": fact_fixture,
        "snap_player_day": snap_player_day,
        "dim_setpieces": dim_setpieces,
        "fact_player_season": fact_player_season
        if not fact_player_season.empty
        else pd.DataFrame(
            columns=["player_code", "season", "start_cost", "end_cost", "total_points", "minutes"]
        ),
        "fact_player_season_opta": fact_player_season_opta,
        "fact_player_style": fact_player_style,
        "dim_style_cluster": dim_style_cluster,
        "fact_player_cluster": fact_player_cluster,
        **derived,
    }
    print("validating...")
    _write_outputs(tables, extra)


if __name__ == "__main__":
    build_marts()
