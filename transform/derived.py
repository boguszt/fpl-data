"""Derived marts: team aggregates, shares, availability, per-90, shrinkage."""

from __future__ import annotations

import hashlib

import pandas as pd
import numpy as np

from ingest.client import season_from_bootstrap

GW_TO_METRIC = {
    "expected_goals": "xg",
    "expected_assists": "xa",
    "expected_goal_involvements": "xgi",
    "expected_goals_conceded": "xgc",
    "goals": "goals",
    "assists": "assists",
    "minutes": "minutes",
    "tackles": "tackles",
    "recoveries": "recoveries",
    "clearances_blocks_interceptions": "clearances_blocks_interceptions",
    "defensive_contribution": "defensive_contribution",
    "bps": "bps",
    "saves": "saves",
    "goals_conceded": "goals_conceded",
    "yellow": "yellow_cards",
    "red": "red_cards",
    "own_goals": "own_goals",
    "penalties_missed": "penalties_missed",
    "penalties_saved": "penalties_saved",
    "clean_sheets": "clean_sheets",
    "bonus": "bonus",
    "total_points": "total_points",
    "starts": "starts",
}

TEAM_METRICS = [
    "xg",
    "xa",
    "xgi",
    "goals",
    "assists",
    "minutes",
    "tackles",
    "recoveries",
    "clearances_blocks_interceptions",
    "defensive_contribution",
    "bps",
    "saves",
]

SHARE_METRICS = [
    "xg",
    "xa",
    "xgi",
    "goals",
    "assists",
    "tackles",
    "recoveries",
    "clearances_blocks_interceptions",
    "defensive_contribution",
]

P90_METRICS = [
    "xg",
    "xa",
    "xgi",
    "xgc",
    "goals",
    "assists",
    "tackles",
    "recoveries",
    "clearances_blocks_interceptions",
    "defensive_contribution",
    "bps",
    "saves",
    "goals_conceded",
    "yellow_cards",
    "red_cards",
    "own_goals",
    "penalties_missed",
    "penalties_saved",
    "clean_sheets",
    "bonus",
    "total_points",
]

FALLBACK_K = 10.0
MIN_SPLIT_PAIRS = 20
KEEPER_METRICS = {"saves", "penalties_saved"}
SHRINK_SEED = b"fpl-adj90-v1"


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def _to_int(s: pd.Series) -> pd.Series:
    return _num(s).astype("Int64")


def _sum_na(s: pd.Series):
    return _num(s).sum(min_count=1)


def _positions(raw: str | None) -> set[int]:
    if raw is None or (isinstance(raw, float) and pd.isna(raw)):
        return {1, 2, 3, 4}
    return {int(p) for p in str(raw).split(",") if p.strip()}


def rename_gw_metrics(fact_player_gw: pd.DataFrame) -> pd.DataFrame:
    keep = ["season", "gw", "player_code", "team_code"]
    for extra in ("appearances", "appearances_60_plus"):
        if extra in fact_player_gw.columns:
            keep.append(extra)
    cols = {src: dest for src, dest in GW_TO_METRIC.items() if src in fact_player_gw.columns}
    out = fact_player_gw[keep + list(cols)].rename(columns=cols).copy()
    for dest in GW_TO_METRIC.values():
        if dest not in out.columns:
            out[dest] = pd.NA
        elif dest != "minutes":
            out[dest] = _num(out[dest])
    out["minutes"] = _num(out["minutes"])
    if "appearances" in out.columns:
        out["appearances"] = _num(out["appearances"])
    if "appearances_60_plus" in out.columns:
        out["appearances_60_plus"] = _num(out["appearances_60_plus"])
    out["gw"] = _to_int(out["gw"])
    out["player_code"] = _to_int(out["player_code"])
    out["team_code"] = _to_int(out["team_code"])
    return out


def season_element_type(players_raw: pd.DataFrame, bootstrap: dict) -> pd.DataFrame:
    pr = players_raw.copy()
    pr["player_code"] = _to_int(pr["code"])
    pr["element_type"] = _to_int(pr["element_type"])
    out = (
        pr.dropna(subset=["player_code", "season"])
        .drop_duplicates(subset=["season", "player_code"], keep="last")[
            ["season", "player_code", "element_type"]
        ]
    )
    current = season_from_bootstrap(bootstrap)
    boot = pd.DataFrame(
        [
            {
                "season": current,
                "player_code": el.get("code"),
                "element_type": el.get("element_type"),
            }
            for el in bootstrap.get("elements") or []
        ]
    )
    if not boot.empty:
        boot["player_code"] = _to_int(boot["player_code"])
        boot["element_type"] = _to_int(boot["element_type"])
        out = (
            pd.concat([out, boot], ignore_index=True)
            .drop_duplicates(subset=["season", "player_code"], keep="last")
        )
    out["season"] = out["season"].astype(str)
    return out.reset_index(drop=True)


def parse_scoreline(result) -> tuple[int | None, int | None]:
    """Parse fact_fixture.result `'2-1'` into (home, away). Unplayed -> (None, None)."""
    if result is None:
        return None, None
    try:
        if pd.isna(result):
            return None, None
    except (ValueError, TypeError):
        pass
    text = str(result).strip()
    if not text or "-" not in text:
        return None, None
    left, right = text.split("-", 1)
    try:
        return int(left), int(right)
    except ValueError:
        return None, None


def _defensive_from_fixtures(
    fact_fixture: pd.DataFrame,
    fact_player_fixture: pd.DataFrame | None,
) -> pd.DataFrame:
    """Per (season, gw, team_code): goals_conceded, clean_sheets, xgc from matches.

    gc/cs come from the scoreline. xgc is the opponent's attacking xG in that
    fixture (player-sum xG of the other side), never FPL's per-player xGC.
    """
    empty = pd.DataFrame(
        columns=["season", "gw", "team_code", "goals_conceded", "clean_sheets", "xgc"]
    )
    if fact_fixture.empty:
        return empty
    fx = fact_fixture.copy()
    fx["gw"] = _to_int(fx["gw"])
    fx["home_team_code"] = _to_int(fx["home_team_code"])
    fx["away_team_code"] = _to_int(fx["away_team_code"])
    scores = [parse_scoreline(v) for v in fx["result"].tolist()]
    fx["home_score"] = pd.array([s[0] for s in scores], dtype="Int64")
    fx["away_score"] = pd.array([s[1] for s in scores], dtype="Int64")
    played = fx.loc[fx["home_score"].notna() & fx["away_score"].notna()].copy()
    if played.empty:
        return empty

    xg_col = None
    xg_by_side = pd.DataFrame(columns=["season", "fixture_id", "team_code", "xg_for"])
    if fact_player_fixture is not None and not fact_player_fixture.empty:
        work = fact_player_fixture.copy()
        if "xg" in work.columns:
            xg_col = "xg"
        elif "expected_goals" in work.columns:
            xg_col = "expected_goals"
        if xg_col is not None:
            work["fixture_id"] = _to_int(work["fixture_id"])
            work["team_code"] = _to_int(work["team_code"])
            work = work.dropna(subset=["fixture_id", "team_code"])
            xg_by_side = (
                work.groupby(["season", "fixture_id", "team_code"], dropna=False)[xg_col]
                .apply(_sum_na)
                .reset_index()
                .rename(columns={xg_col: "xg_for"})
            )
            xg_by_side["season"] = xg_by_side["season"].astype(str)

    def _side(
        src: pd.DataFrame,
        team_col: str,
        opp_col: str,
        gf_col: str,
        ga_col: str,
    ) -> pd.DataFrame:
        side = src[
            ["season", "gw", "fixture_id", team_col, opp_col, gf_col, ga_col]
        ].rename(
            columns={
                team_col: "team_code",
                opp_col: "opp_code",
                gf_col: "goals_for",
                ga_col: "goals_conceded",
            }
        )
        side["clean_sheets"] = (side["goals_conceded"] == 0).astype("Int64")
        if xg_by_side.empty:
            side["xgc"] = pd.NA
            return side
        opp_xg = xg_by_side.rename(
            columns={"team_code": "opp_code", "xg_for": "xgc"}
        )
        side = side.merge(
            opp_xg, on=["season", "fixture_id", "opp_code"], how="left"
        )
        return side

    stacked = pd.concat(
        [
            _side(played, "home_team_code", "away_team_code", "home_score", "away_score"),
            _side(played, "away_team_code", "home_team_code", "away_score", "home_score"),
        ],
        ignore_index=True,
    )
    stacked["season"] = stacked["season"].astype(str)
    stacked["gw"] = _to_int(stacked["gw"])
    stacked["team_code"] = _to_int(stacked["team_code"])
    out = (
        stacked.groupby(["season", "gw", "team_code"], dropna=False)
        .agg(
            goals_conceded=("goals_conceded", _sum_na),
            clean_sheets=("clean_sheets", _sum_na),
            xgc=("xgc", _sum_na),
        )
        .reset_index()
    )
    out["goals_conceded"] = _to_int(out["goals_conceded"])
    out["clean_sheets"] = _to_int(out["clean_sheets"])
    return out


def build_fact_team_gw(
    gw: pd.DataFrame,
    fact_fixture: pd.DataFrame,
    fact_player_fixture: pd.DataFrame | None = None,
) -> pd.DataFrame:
    work = gw.dropna(subset=["team_code"]).copy()
    agg = {m: _sum_na for m in TEAM_METRICS if m in work.columns}
    team = work.groupby(["season", "gw", "team_code"], dropna=False).agg(agg).reset_index()

    matches = pd.DataFrame(columns=["season", "gw", "team_code", "matches_played"])
    if not fact_fixture.empty:
        fx = fact_fixture.copy()
        fx["gw"] = _to_int(fx["gw"])
        fx["home_team_code"] = _to_int(fx["home_team_code"])
        fx["away_team_code"] = _to_int(fx["away_team_code"])
        played = fx.loc[fx["result"].notna() & fx["gw"].notna()]
        home = played[["season", "gw", "home_team_code", "fixture_id"]].rename(
            columns={"home_team_code": "team_code"}
        )
        away = played[["season", "gw", "away_team_code", "fixture_id"]].rename(
            columns={"away_team_code": "team_code"}
        )
        stacked = pd.concat([home, away], ignore_index=True).dropna(subset=["team_code"])
        stacked["team_code"] = _to_int(stacked["team_code"])
        matches = (
            stacked.drop_duplicates(subset=["season", "gw", "team_code", "fixture_id"])
            .groupby(["season", "gw", "team_code"], as_index=False)
            .size()
            .rename(columns={"size": "matches_played"})
        )

    team = team.merge(matches, on=["season", "gw", "team_code"], how="left")
    team["matches_played"] = _to_int(team["matches_played"])
    fallback = team["matches_played"].isna() & (team["minutes"].fillna(0) > 0)
    team.loc[fallback, "matches_played"] = 1
    team["matches_played"] = team["matches_played"].fillna(0).astype("Int64")
    defensive = _defensive_from_fixtures(fact_fixture, fact_player_fixture)
    team = team.drop(columns=["xgc", "goals_conceded", "clean_sheets"], errors="ignore")
    team = team.merge(defensive, on=["season", "gw", "team_code"], how="left")
    team["goals_conceded"] = _to_int(team["goals_conceded"])
    team["clean_sheets"] = _to_int(team["clean_sheets"])
    team["season"] = team["season"].astype(str)
    team["gw"] = _to_int(team["gw"])
    team["team_code"] = _to_int(team["team_code"])
    return team


def _p90(total: pd.Series, minutes: pd.Series) -> pd.Series:
    minutes = _num(minutes)
    total = _num(total)
    out = pd.Series(np.nan, index=total.index, dtype="float64")
    ok = minutes.notna() & (minutes > 0) & total.notna()
    out.loc[ok] = total.loc[ok] / minutes.loc[ok] * 90.0
    return out


def _split_seed(*parts) -> int:
    raw = SHRINK_SEED + b"|" + b"|".join(str(p).encode() for p in parts)
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], "little") % (2**32)


def _k_from_pairs(rate_a: list[float], rate_b: list[float], n_half: list[float]) -> tuple[float, str, int, float | None]:
    n_pairs = len(rate_a)
    if n_pairs < MIN_SPLIT_PAIRS:
        return FALLBACK_K, "fallback", n_pairs, None
    xa = np.asarray(rate_a, dtype="float64")
    xb = np.asarray(rate_b, dtype="float64")
    if float(np.std(xa)) < 1e-12 or float(np.std(xb)) < 1e-12:
        return FALLBACK_K, "fallback", n_pairs, None
    r = float(np.corrcoef(xa, xb)[0, 1])
    if not np.isfinite(r) or r <= 0 or r > 1:
        return FALLBACK_K, "fallback", n_pairs, r if np.isfinite(r) else None
    k = float(np.mean(n_half)) * (1.0 - r) / r
    if not np.isfinite(k) or k <= 0:
        return FALLBACK_K, "fallback", n_pairs, r
    return k, "split_half", n_pairs, r


def estimate_shrinkage(
    gw: pd.DataFrame,
    positions: pd.DataFrame,
    metric_direction: pd.DataFrame,
) -> pd.DataFrame:
    """k per (metric, element_type), stored on every (season, element_type, metric)."""
    direction = {
        row["metric_name"]: _positions(row["applies_to_positions"])
        for _, row in metric_direction.iterrows()
    }
    work = gw.merge(positions, on=["season", "player_code"], how="left")
    work = work.dropna(subset=["player_code", "element_type", "gw"])
    work["element_type"] = _to_int(work["element_type"])
    work["gw"] = _to_int(work["gw"])
    present = [m for m in P90_METRICS if m in work.columns]
    work = work.sort_values(["season", "player_code", "gw"], kind="mergesort")
    work["rn"] = work.groupby(["season", "player_code"], sort=False).cumcount()
    key = work["season"].astype(str) + "|" + work["player_code"].astype(str)
    offset = pd.util.hash_pandas_object(key, index=False).to_numpy() % 2
    work["half"] = (work["rn"].to_numpy() + offset) % 2

    agg = {"minutes": ("minutes", "sum")}
    for m in present:
        agg[m] = (m, "sum")
    halves = (
        work.groupby(["season", "player_code", "element_type", "half"], dropna=False)
        .agg(**agg)
        .unstack("half")
    )
    minutes = halves["minutes"]
    if 0 not in minutes.columns or 1 not in minutes.columns:
        minutes = minutes.reindex(columns=[0, 1])
    ma = _num(minutes[0])
    mb = _num(minutes[1])
    both = (ma > 0) & (mb > 0)
    n_half = 0.5 * (ma + mb) / 90.0
    et = halves.index.get_level_values("element_type")

    seasons = sorted({str(s) for s in work["season"].dropna().unique()})
    fallbacks: list[str] = []
    rows = []
    for metric in present:
        allowed = direction.get(metric, {1, 2, 3, 4})
        if metric not in halves.columns.get_level_values(0):
            continue
        tot = halves[metric].reindex(columns=[0, 1])
        ta = _num(tot[0])
        tb = _num(tot[1])
        usable = both & ta.notna() & tb.notna()
        rate_a = ta / ma * 90.0
        rate_b = tb / mb * 90.0
        for et_int in sorted(allowed):
            if metric in KEEPER_METRICS and et_int != 1:
                continue
            mask = usable & (et == et_int)
            k, method, n_pairs, r = _k_from_pairs(
                rate_a.loc[mask].tolist(),
                rate_b.loc[mask].tolist(),
                n_half.loc[mask].tolist(),
            )
            if method == "fallback":
                reason = f"n_pairs={n_pairs}"
                if r is not None:
                    reason += f" r={r:.3f}"
                fallbacks.append(f"{metric} et={et_int} ({reason})")
            for season in seasons:
                rows.append(
                    {
                        "season": season,
                        "element_type": et_int,
                        "metric": metric,
                        "k": k,
                        "method": method,
                    }
                )
    if fallbacks:
        print("  shrinkage fallback k=10:", flush=True)
        for line in fallbacks:
            print(f"    {line}", flush=True)
    else:
        print("  shrinkage: all metrics fitted from split-half", flush=True)

    out = pd.DataFrame(rows)
    if out.empty:
        return pd.DataFrame(columns=["season", "element_type", "metric", "k", "method"])
    out["season"] = out["season"].astype(str)
    out["element_type"] = _to_int(out["element_type"])
    out["k"] = _num(out["k"])
    return out


def _add_adj90(metrics: pd.DataFrame, shrinkage: pd.DataFrame) -> pd.DataFrame:
    out = metrics.copy()
    season_g = out.loc[out["grain"] == "season"]
    shrink = shrinkage.copy() if not shrinkage.empty else pd.DataFrame(
        columns=["season", "element_type", "metric", "k"]
    )
    shrink["element_type"] = _to_int(shrink["element_type"])

    for metric in P90_METRICS:
        tot_col = f"{metric}_total"
        out[f"{metric}_adj90"] = np.nan
        if tot_col not in out.columns:
            continue
        mu_rows = []
        block = season_g.loc[
            season_g["minutes_total"].fillna(0) > 0,
            ["season", "element_type", "minutes_total", tot_col],
        ].copy()
        block[tot_col] = _num(block[tot_col])
        block = block.loc[block[tot_col].notna() & block["element_type"].notna()]
        for (season, et), g in block.groupby(["season", "element_type"], dropna=False):
            minutes = float(_num(g["minutes_total"]).sum())
            if minutes <= 0:
                continue
            mu_rows.append(
                {
                    "season": season,
                    "element_type": int(et),
                    "mu": float(_num(g[tot_col]).sum()) / minutes * 90.0,
                }
            )
        mu_df = pd.DataFrame(mu_rows)
        k_df = shrink.loc[shrink["metric"] == metric, ["season", "element_type", "k"]]
        tmp = out[["season", "element_type", "minutes_total", tot_col]].copy()
        tmp["element_type"] = _to_int(tmp["element_type"])
        if not mu_df.empty:
            tmp = tmp.merge(mu_df, on=["season", "element_type"], how="left")
        else:
            tmp["mu"] = np.nan
        if not k_df.empty:
            tmp = tmp.merge(k_df, on=["season", "element_type"], how="left")
        else:
            tmp["k"] = np.nan
        minutes = _num(tmp["minutes_total"])
        total = _num(tmp[tot_col])
        nineties = minutes / 90.0
        k = _num(tmp["k"]).fillna(FALLBACK_K)
        mu = _num(tmp["mu"])
        ok = (minutes > 0) & total.notna() & mu.notna() & (nineties + k > 0)
        if metric in KEEPER_METRICS:
            ok &= tmp["element_type"] == 1
        adj = pd.Series(np.nan, index=tmp.index, dtype="float64")
        adj.loc[ok] = (total.loc[ok] + k.loc[ok] * mu.loc[ok]) / (nineties.loc[ok] + k.loc[ok])
        out[f"{metric}_adj90"] = adj.to_numpy()
    return out


def _share(player: pd.Series, team: pd.Series) -> pd.Series:
    player = _num(player)
    team = _num(team)
    out = pd.Series(np.nan, index=player.index, dtype="float64")
    ok = team.notna() & (team != 0) & player.notna()
    out.loc[ok] = player.loc[ok] / team.loc[ok]
    return out


def _weighted_share(g: pd.DataFrame, metric: str) -> float:
    w = _num(g["minutes_total"])
    s = _num(g[f"{metric}_share"])
    mask = w.notna() & (w > 0) & s.notna()
    if not mask.any():
        return float("nan")
    return float((s[mask] * w[mask]).sum() / w[mask].sum())


def _uncovered_player_gws(gw: pd.DataFrame, covered: pd.DataFrame) -> pd.DataFrame:
    """GW rows for player-seasons that never have a team_code."""
    orphan = gw.loc[gw["player_code"].notna() & gw["team_code"].isna()].copy()
    if orphan.empty or covered.empty:
        return orphan
    keys = covered[["season", "player_code"]].drop_duplicates().assign(_covered=1)
    orphan = orphan.merge(keys, on=["season", "player_code"], how="left")
    return orphan.loc[orphan["_covered"].isna()].drop(columns=["_covered"])


def _season_metrics_without_team(gw: pd.DataFrame) -> pd.DataFrame:
    """Season grain only: totals exist, shares and spells do not."""
    if gw.empty:
        return pd.DataFrame()
    agg: dict[str, object] = {"minutes_total": ("minutes", _sum_na)}
    for m in P90_METRICS:
        if m in gw.columns:
            agg[f"{m}_total"] = (m, _sum_na)
    out = gw.groupby(["season", "player_code"], dropna=False).agg(**agg).reset_index()
    out["grain"] = "season"
    out["team_code"] = pd.NA
    out["spell_count"] = pd.NA
    for m in SHARE_METRICS:
        out[f"{m}_share"] = pd.NA
    return out


def build_player_metrics(
    gw: pd.DataFrame,
    fact_team_gw: pd.DataFrame,
    positions: pd.DataFrame,
    metric_direction: pd.DataFrame,
) -> pd.DataFrame:
    team_share_cols = ["season", "gw", "team_code"] + [
        m for m in SHARE_METRICS if m in fact_team_gw.columns
    ]
    team = fact_team_gw[team_share_cols].rename(
        columns={m: f"{m}_team" for m in SHARE_METRICS if m in fact_team_gw.columns}
    )
    keyed = gw.merge(team, on=["season", "gw", "team_code"], how="left")
    keyed = keyed.dropna(subset=["player_code"])
    merged = keyed.dropna(subset=["team_code"])

    agg: dict[str, object] = {"minutes_total": ("minutes", _sum_na)}
    for m in P90_METRICS:
        if m in merged.columns:
            agg[f"{m}_total"] = (m, _sum_na)
    for m in SHARE_METRICS:
        team_col = f"{m}_team"
        if m in merged.columns and team_col in merged.columns:
            agg[f"{m}_team_sum"] = (team_col, _sum_na)

    if merged.empty:
        spell = pd.DataFrame()
        season_df = pd.DataFrame()
    else:
        spell = merged.groupby(["season", "player_code", "team_code"], dropna=False).agg(
            **{k: v for k, v in agg.items()}
        ).reset_index()
        spell["grain"] = "spell"
        for m in SHARE_METRICS:
            total_col = f"{m}_total"
            team_col = f"{m}_team_sum"
            if total_col in spell.columns and team_col in spell.columns:
                spell[f"{m}_share"] = _share(spell[total_col], spell[team_col])
            else:
                spell[f"{m}_share"] = pd.NA

        n_teams = spell.groupby(["season", "player_code"])["team_code"].transform("nunique")
        spell["spell_count"] = _to_int(n_teams)

        season_rows = []
        for (season, player_code), g in spell.groupby(["season", "player_code"], sort=False):
            row = {
                "season": season,
                "player_code": player_code,
                "team_code": pd.NA,
                "grain": "season",
                "spell_count": int(g["spell_count"].iloc[0]),
                "minutes_total": _sum_na(g["minutes_total"]),
            }
            for m in P90_METRICS:
                col = f"{m}_total"
                if col in g.columns:
                    row[col] = _sum_na(g[col])
            for m in SHARE_METRICS:
                row[f"{m}_share"] = _weighted_share(g, m)
            season_rows.append(row)
        season_df = pd.DataFrame(season_rows) if season_rows else spell.iloc[0:0].copy()

    parts = [df for df in (spell, season_df) if df is not None and not df.empty]
    orphan = _season_metrics_without_team(_uncovered_player_gws(keyed, merged))
    if not orphan.empty:
        parts.append(orphan)
    if not parts:
        metrics = pd.DataFrame()
    else:
        metrics = pd.concat(parts, ignore_index=True, sort=False)
    drop_team_sums = [c for c in metrics.columns if c.endswith("_team_sum")]
    metrics = metrics.drop(columns=drop_team_sums, errors="ignore")

    for m in P90_METRICS:
        total_col = f"{m}_total"
        if total_col not in metrics.columns:
            metrics[total_col] = pd.NA
        metrics[f"{m}_p90"] = _p90(metrics[total_col], metrics["minutes_total"])

    metrics = metrics.merge(positions, on=["season", "player_code"], how="left")
    shrinkage = estimate_shrinkage(gw, positions, metric_direction)
    metrics = _add_adj90(metrics, shrinkage)
    metrics["season"] = metrics["season"].astype(str)
    metrics["player_code"] = _to_int(metrics["player_code"])
    metrics["team_code"] = _to_int(metrics["team_code"])
    metrics["spell_count"] = _to_int(metrics["spell_count"])
    metrics["element_type"] = _to_int(metrics["element_type"])
    metrics["minutes_total"] = _num(metrics["minutes_total"])
    return metrics, shrinkage


def _season_availability_without_team(gw: pd.DataFrame) -> pd.DataFrame:
    if gw.empty:
        return pd.DataFrame()
    work = gw.copy()
    if "appearances" not in work.columns:
        work["appearances"] = (work["minutes"].fillna(0) > 0).astype("int64")
    if "appearances_60_plus" not in work.columns:
        work["appearances_60_plus"] = (work["minutes"].fillna(0) >= 60).astype("int64")
    if "starts" not in work.columns:
        work["starts"] = pd.NA
    out = (
        work.groupby(["season", "player_code"], dropna=False)
        .agg(
            minutes=("minutes", _sum_na),
            appearances=("appearances", _sum_na),
            starts=("starts", _sum_na),
            appearances_60_plus=("appearances_60_plus", _sum_na),
        )
        .reset_index()
    )
    out["grain"] = "season"
    out["team_code"] = pd.NA
    out["spell_count"] = pd.NA
    out["team_matches"] = pd.NA
    return out


def build_availability(
    gw: pd.DataFrame,
    fact_team_gw: pd.DataFrame,
    dim_setpieces: pd.DataFrame,
    positions: pd.DataFrame,
) -> pd.DataFrame:
    keyed = gw.dropna(subset=["player_code"]).copy()
    work = keyed.dropna(subset=["team_code"])
    mp = fact_team_gw[["season", "gw", "team_code", "matches_played"]].copy()
    work = work.merge(mp, on=["season", "gw", "team_code"], how="left")
    if "appearances" not in work.columns:
        work["appearances"] = (work["minutes"].fillna(0) > 0).astype("int64")
    if "appearances_60_plus" not in work.columns:
        work["appearances_60_plus"] = (work["minutes"].fillna(0) >= 60).astype("int64")
    work["appearances"] = _num(work["appearances"]).fillna(0)
    work["appearances_60_plus"] = _num(work["appearances_60_plus"]).fillna(0)

    if work.empty:
        spell = pd.DataFrame()
        season = pd.DataFrame()
    else:
        spell = work.groupby(["season", "player_code", "team_code"], dropna=False).agg(
            minutes=("minutes", _sum_na),
            appearances=("appearances", _sum_na),
            starts=("starts", _sum_na),
            appearances_60_plus=("appearances_60_plus", _sum_na),
            team_matches=("matches_played", _sum_na),
        ).reset_index()
        spell["grain"] = "spell"
        n_teams = spell.groupby(["season", "player_code"])["team_code"].transform("nunique")
        spell["spell_count"] = _to_int(n_teams)

        season = (
            spell.groupby(["season", "player_code"], dropna=False)
            .agg(
                minutes=("minutes", _sum_na),
                appearances=("appearances", "sum"),
                starts=("starts", _sum_na),
                appearances_60_plus=("appearances_60_plus", "sum"),
                team_matches=("team_matches", _sum_na),
                spell_count=("spell_count", "max"),
            )
            .reset_index()
        )
        season["team_code"] = pd.NA
        season["grain"] = "season"

    parts = [df for df in (spell, season) if df is not None and not df.empty]
    orphan = _season_availability_without_team(_uncovered_player_gws(keyed, work))
    if not orphan.empty:
        parts.append(orphan)
    if not parts:
        av = pd.DataFrame(
            columns=[
                "grain",
                "season",
                "player_code",
                "team_code",
                "spell_count",
                "minutes",
                "appearances",
                "starts",
                "appearances_60_plus",
                "team_matches",
            ]
        )
    else:
        av = pd.concat(parts, ignore_index=True, sort=False)
    av["team_minutes_available"] = _num(av["team_matches"]) * 90.0
    av["minutes_per_appearance"] = pd.Series(np.nan, index=av.index, dtype="float64")
    ok_app = _num(av["appearances"]).fillna(0) > 0
    av.loc[ok_app, "minutes_per_appearance"] = (
        _num(av.loc[ok_app, "minutes"]) / _num(av.loc[ok_app, "appearances"])
    )
    av["minutes_share"] = pd.Series(np.nan, index=av.index, dtype="float64")
    ok_tm = av["team_minutes_available"].fillna(0) > 0
    av.loc[ok_tm, "minutes_share"] = (
        _num(av.loc[ok_tm, "minutes"]) / av.loc[ok_tm, "team_minutes_available"]
    )

    sp = dim_setpieces.copy()
    if not sp.empty:
        sp = sp.rename(columns={"player_code": "player_code"})
        keep = [
            "player_code",
            "penalties_order",
            "direct_freekicks_order",
            "corners_and_indirect_freekicks_order",
        ]
        av = av.merge(sp[keep], on="player_code", how="left")
    else:
        av["penalties_order"] = pd.NA
        av["direct_freekicks_order"] = pd.NA
        av["corners_and_indirect_freekicks_order"] = pd.NA

    av = av.merge(positions, on=["season", "player_code"], how="left")
    av["season"] = av["season"].astype(str)
    av["player_code"] = _to_int(av["player_code"])
    av["team_code"] = _to_int(av["team_code"])
    av["spell_count"] = _to_int(av["spell_count"])
    av["element_type"] = _to_int(av["element_type"])
    av["appearances"] = _to_int(av["appearances"])
    av["appearances_60_plus"] = _to_int(av["appearances_60_plus"])
    av["starts"] = _to_int(av["starts"])
    for col in (
        "penalties_order",
        "direct_freekicks_order",
        "corners_and_indirect_freekicks_order",
    ):
        av[col] = _to_int(av[col])
    ordered = [
        "grain",
        "season",
        "player_code",
        "team_code",
        "spell_count",
        "element_type",
        "minutes",
        "appearances",
        "starts",
        "appearances_60_plus",
        "minutes_per_appearance",
        "team_minutes_available",
        "minutes_share",
        "penalties_order",
        "direct_freekicks_order",
        "corners_and_indirect_freekicks_order",
    ]
    return av[ordered]


def current_player_codes(bootstrap: dict) -> pd.DataFrame:
    codes = [el.get("code") for el in bootstrap.get("elements") or []]
    df = pd.DataFrame({"player_code": codes})
    df["player_code"] = _to_int(df["player_code"])
    return df.drop_duplicates()


def build_derived_tables(
    fact_player_gw: pd.DataFrame,
    fact_fixture: pd.DataFrame,
    dim_setpieces: pd.DataFrame,
    players_raw: pd.DataFrame,
    bootstrap: dict,
    metric_direction: pd.DataFrame,
    region_lookup: pd.DataFrame,
    fact_player_fixture: pd.DataFrame | None = None,
) -> dict[str, pd.DataFrame]:
    gw = rename_gw_metrics(fact_player_gw)
    positions = season_element_type(players_raw, bootstrap)
    fact_team_gw = build_fact_team_gw(gw, fact_fixture, fact_player_fixture)
    print(f"  fact_team_gw {len(fact_team_gw)}", flush=True)
    metrics, shrinkage = build_player_metrics(gw, fact_team_gw, positions, metric_direction)
    print(f"  fact_player_season_metrics {len(metrics)}", flush=True)
    print(f"  dim_shrinkage {len(shrinkage)}", flush=True)
    availability = build_availability(gw, fact_team_gw, dim_setpieces, positions)
    print(f"  fact_player_season_availability {len(availability)}", flush=True)
    dim_metric = metric_direction.copy()
    dim_region = region_lookup.copy()
    return {
        "fact_team_gw": fact_team_gw,
        "fact_player_season_metrics": metrics,
        "fact_player_season_availability": availability,
        "dim_metric": dim_metric,
        "dim_region": dim_region,
        "dim_shrinkage": shrinkage,
        "_current_players": current_player_codes(bootstrap),
    }
