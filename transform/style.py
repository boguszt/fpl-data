"""Scale-free player-style features and k-means role clustering."""

from __future__ import annotations

import re

import numpy as np
import pandas as pd

from ingest.paths import DB_PATH, MARTS
from transform.load import load_style_features

FIT_FROM = "2019-20"
MIN_TOUCHES = 200
MIN_PASSES = 200
OMITTED_ZERO_SHARE = 0.50
MIN_Z_GROUP = 3
K_RANGE = range(4, 13)
CHOSEN_K = 9
RANDOM_STATE = 42
# Goalkeepers (and FPL managers) are dropped from k-means, not fitted separately.
# Their shooting / take-on space barely overlaps with outfielders.
EXCLUDE_FROM_CLUSTERING = {1, 5}
CLUSTER_META = {
    0: ("Generalist", "no dimension above ±0.15 — balanced, low-volume"),
    1: ("Wide deliverer", "crosses, forward-zone passes"),
    2: (
        "Between the lines",
        "backward passes, losses, progressive carries, box touches",
    ),
    3: ("Aerial defender", "headed shots, clearances, aerials"),
    4: ("Shooter", "shot share, shots per touch, box touches"),
    5: ("Dribbler", "take-ons, box touches, final-third touches"),
    6: (
        "Deep recycler",
        "forward and long passes, low final third, low losses",
    ),
    7: (
        "Progressive distributor",
        "forward passes, long balls, through balls, high touch share",
    ),
    8: (
        "Ball-playing defender",
        "aerial defender profile plus high team pass and touch share",
    ),
}
CLUSTER_NOTES = (
    "Clusters 3 and 8 are separated largely by team pass and touch share, so "
    "the split partly reflects team possession role rather than individual "
    "technique. Do not present it as purely a player attribute.\n"
    "The model is fitted on 682 player-seasons, 658 of them from 2024-25 and "
    "2025-26, because carries and final-third touches are not populated "
    "league-wide before 2024-25. It is effectively a two-season model and will "
    "deepen by one season per year.\n"
    "Goalkeepers and managers are excluded from the fit. Their style rows "
    "exist but carry no cluster."
)
CLUSTER_NOTE_LIST = [p.strip() for p in CLUSTER_NOTES.split("\n") if p.strip()]
POS_LABEL = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD", 5: "MGR"}
_IDENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_D_PARTS = ("total_tackle", "interception", "effective_clearance", "ball_recovery")
_TEAM_DENOMS = {
    "team_total_pass": "total_pass",
    "team_total_scoring_att": "total_scoring_att",
    "team_touches": "touches",
}


def _num(s: pd.Series) -> pd.Series:
    return pd.to_numeric(s, errors="coerce")


def _to_int(s: pd.Series) -> pd.Series:
    return _num(s).astype("Int64")


def _formula_cols(expr: str) -> list[str]:
    expr = (expr or "").strip()
    if expr == "D":
        return list(_D_PARTS)
    if expr in _TEAM_DENOMS:
        return [_TEAM_DENOMS[expr]]
    parts = [p.strip() for p in expr.split("+")]
    for part in parts:
        if not _IDENT.match(part):
            raise ValueError(f"bad style formula token: {part!r} in {expr!r}")
    return parts


def count_columns(spec: pd.DataFrame) -> list[str]:
    cols: list[str] = []
    seen: set[str] = set()
    for _, row in spec.iterrows():
        for expr in (row["numerator"], row["denominator"]):
            for col in _formula_cols(str(expr)):
                if col not in seen:
                    seen.add(col)
                    cols.append(col)
    return cols


def fill_omitted_zeros(opta: pd.DataFrame, cols: list[str]) -> pd.DataFrame:
    """Pulse omits a stat when the value is 0.

    Fill 0 in a season when the field is present for enough of the squad that
    this is an omitted-zero, or when an *earlier* season already cleared that
    bar and this season has at least one value (in-progress years: almost
    nobody has a through-ball yet). Leave NULL when the schema is absent
    (carries in 2020-21 / 2022-23) — do not back-fill from a later season.
    """
    out = opta.copy()
    seasons = sorted(out["season"].astype(str).unique())
    for col in cols:
        if col not in out.columns:
            out[col] = pd.NA
            continue
        share = out.groupby("season")[col].apply(
            lambda s: float(s.notna().mean()) if len(s) else 0.0
        )
        established = False
        fill_seasons: set[str] = set()
        for season in seasons:
            sh = float(share.get(season, 0.0) or 0.0)
            if sh >= OMITTED_ZERO_SHARE:
                established = True
                fill_seasons.add(season)
            elif established and sh > 0:
                fill_seasons.add(season)
        filled = _num(out[col])
        mask = out["season"].astype(str).isin(fill_seasons)
        filled = filled.where(~mask, filled.fillna(0.0))
        out[col] = filled
    return out


def _eval_expr(df: pd.DataFrame, expr: str) -> pd.Series:
    expr = str(expr).strip()
    if expr == "D":
        parts = [
            _num(df[c]) if c in df.columns else pd.Series(pd.NA, index=df.index)
            for c in _D_PARTS
        ]
        acc = parts[0]
        any_present = parts[0].notna()
        for p in parts[1:]:
            acc = acc.add(p, fill_value=0)
            any_present = any_present | p.notna()
        return acc.where(any_present)
    if expr in _TEAM_DENOMS:
        return _num(df[expr])
    parts = [p.strip() for p in expr.split("+")]
    if len(parts) == 1:
        col = parts[0]
        if col not in df.columns:
            return pd.Series(pd.NA, index=df.index)
        return _num(df[col])
    acc = None
    any_present = pd.Series(False, index=df.index)
    for col in parts:
        if col not in df.columns:
            piece = pd.Series(pd.NA, index=df.index)
        else:
            piece = _num(df[col])
        any_present = any_present | piece.notna()
        acc = piece if acc is None else acc.add(piece, fill_value=0)
    return acc.where(any_present)


def _apply_floor(df: pd.DataFrame, spec_row: pd.Series, values: pd.Series) -> pd.Series:
    """NULL when the denominator is 0 or below the family floor."""
    den = _eval_expr(df, spec_row["denominator"])
    cat = str(spec_row["category"])
    name = str(spec_row["name"])
    ok = den.notna() & (den > 0)
    touches = _num(df["touches"]) if "touches" in df.columns else pd.Series(pd.NA, index=df.index)
    passes = _num(df["total_pass"]) if "total_pass" in df.columns else pd.Series(pd.NA, index=df.index)
    if cat == "passing" or name == "pass_share_of_team":
        ok &= passes >= MIN_PASSES
    if cat in {"territory", "on_the_ball"} or name in {
        "shots_per_touch",
        "touch_share_of_team",
        "take_on_rate",
        "loss_rate",
    }:
        ok &= touches >= MIN_TOUCHES
    if name == "progressive_carry_share":
        ok &= touches >= MIN_TOUCHES
    if cat == "team_share":
        spell = _to_int(df["spell_count"])
        ok &= spell.eq(1)
    first = spec_row["first_season"]
    if pd.notna(first) and str(first).strip():
        ok &= df["season"].astype(str) >= str(first)
    return values.where(ok)


def _zscore_within(df: pd.DataFrame, cols: list[str], group_cols: list[str]) -> pd.DataFrame:
    out = df.copy()
    grouped = out.groupby(group_cols, dropna=False)
    for col in cols:
        zcol = f"{col}_z"

        def _z(s: pd.Series) -> pd.Series:
            x = _num(s)
            n = int(x.notna().sum())
            if n < MIN_Z_GROUP:
                return pd.Series(np.nan, index=s.index)
            mu = x.mean()
            sd = x.std(ddof=0)
            if sd is None or not np.isfinite(sd) or sd == 0:
                return pd.Series(np.nan, index=s.index)
            return (x - mu) / sd

        out[zcol] = grouped[col].transform(_z)
    return out


def build_fact_player_style(
    opta: pd.DataFrame,
    availability: pd.DataFrame,
    positions: pd.DataFrame,
    spec: pd.DataFrame | None = None,
) -> pd.DataFrame:
    if spec is None:
        spec = load_style_features()
    if opta.empty:
        return pd.DataFrame(columns=["season", "player_code"])

    counts = count_columns(spec)
    work = opta.copy()
    work["season"] = work["season"].astype(str)
    work["player_code"] = _to_int(work["player_code"])
    work = fill_omitted_zeros(work, counts)

    pos = positions.copy()
    pos["season"] = pos["season"].astype(str)
    pos["player_code"] = _to_int(pos["player_code"])
    pos["element_type"] = _to_int(pos["element_type"])
    pos = pos.drop_duplicates(subset=["season", "player_code"], keep="last")
    work = work.merge(pos, on=["season", "player_code"], how="left")

    av = availability.copy()
    if not av.empty:
        av["season"] = av["season"].astype(str)
        av["player_code"] = _to_int(av["player_code"])
        av["spell_count"] = _to_int(av["spell_count"])
        av["team_code"] = _to_int(av["team_code"])
        season_av = av.loc[av["grain"] == "season", ["season", "player_code", "spell_count"]]
        work = work.merge(season_av, on=["season", "player_code"], how="left")
        single = av.loc[
            (av["grain"] == "spell") & av["spell_count"].eq(1),
            ["season", "player_code", "team_code"],
        ].drop_duplicates(subset=["season", "player_code"], keep="last")
        work = work.merge(single, on=["season", "player_code"], how="left")
    else:
        work["spell_count"] = pd.NA
        work["team_code"] = pd.NA

    for team_col, src in _TEAM_DENOMS.items():
        team_sum = (
            work.loc[work["team_code"].notna()]
            .groupby(["season", "team_code"], dropna=False)[src]
            .sum(min_count=1)
            .rename(team_col)
            .reset_index()
        )
        work = work.merge(team_sum, on=["season", "team_code"], how="left")

    feature_names = [str(n) for n in spec["name"]]
    for _, row in spec.iterrows():
        name = str(row["name"])
        ratio = _eval_expr(work, str(row["numerator"])) / _eval_expr(work, str(row["denominator"]))
        work[name] = _apply_floor(work, row, ratio)

    keep = ["season", "player_code", "element_type", "spell_count", "team_code"] + feature_names
    out = work[keep].copy()
    out = _zscore_within(out, feature_names, ["season", "element_type"])
    out["season"] = out["season"].astype(str)
    out["player_code"] = _to_int(out["player_code"])
    out["element_type"] = _to_int(out["element_type"])
    out["spell_count"] = _to_int(out["spell_count"])
    out["team_code"] = _to_int(out["team_code"])
    for col in feature_names:
        out[col] = _num(out[col])
        out[f"{col}_z"] = _num(out[f"{col}_z"])
    return out.reset_index(drop=True)


def clustering_feature_names(spec: pd.DataFrame) -> list[str]:
    return [str(n) for n, flag in zip(spec["name"], spec["in_clustering"]) if int(flag) == 1]


def clustering_frame(
    style: pd.DataFrame,
    spec: pd.DataFrame | None = None,
    *,
    from_season: str = FIT_FROM,
) -> pd.DataFrame:
    if spec is None:
        spec = load_style_features()
    feats = clustering_feature_names(spec)
    zcols = [f"{n}_z" for n in feats]
    work = style.copy()
    work["season"] = work["season"].astype(str)
    work = work.loc[work["season"] >= from_season]
    # Carries are only league-wide from 2024-25. A handful of earlier payloads
    # contain the key; those strays are not a comparable feature space.
    if "carry_rate" in work.columns and "take_on_rate" in work.columns:
        touch_ok = work["take_on_rate"].notna()
        carry_cov = (
            work.loc[touch_ok]
            .groupby("season")["carry_rate"]
            .apply(lambda s: float(s.notna().mean()) if len(s) else 0.0)
        )
        ok_seasons = set(carry_cov[carry_cov >= OMITTED_ZERO_SHARE].index)
        work = work.loc[work["season"].isin(ok_seasons)]
    et = _to_int(work["element_type"])
    work = work.loc[et.notna() & ~et.isin(list(EXCLUDE_FROM_CLUSTERING))]
    work = work.dropna(subset=zcols)
    return work.reset_index(drop=True)


def _fit_kmeans(frame: pd.DataFrame, spec: pd.DataFrame, k: int):
    from sklearn.cluster import KMeans
    from sklearn.metrics.pairwise import euclidean_distances

    feats = clustering_feature_names(spec)
    zcols = [f"{n}_z" for n in feats]
    x = frame[zcols].to_numpy(dtype=float)
    model = KMeans(n_clusters=k, n_init=10, random_state=RANDOM_STATE)
    model.fit(x)
    dist = euclidean_distances(x, model.cluster_centers_)
    order = np.argsort(dist, axis=1)
    assigned = pd.DataFrame(
        {
            "season": frame["season"].to_numpy(),
            "player_code": frame["player_code"].to_numpy(),
            "element_type": frame["element_type"].to_numpy(),
            "cluster_id": order[:, 0],
            "distance": dist[np.arange(len(frame)), order[:, 0]],
            "second_cluster_id": order[:, 1],
            "second_distance": dist[np.arange(len(frame)), order[:, 1]],
        }
    )
    centers = pd.DataFrame(model.cluster_centers_, columns=feats)
    centers.insert(0, "cluster_id", np.arange(k))
    return assigned, centers, dist, feats


def build_cluster_tables(
    style: pd.DataFrame,
    spec: pd.DataFrame | None = None,
    *,
    k: int = CHOSEN_K,
    from_season: str = FIT_FROM,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Fit one k-means on pooled complete cases, assign every row against it."""
    if spec is None:
        spec = load_style_features()
    if k != CHOSEN_K:
        raise ValueError(f"k must be {CHOSEN_K} (chosen); got {k}")
    if set(CLUSTER_META) != set(range(k)):
        raise ValueError("CLUSTER_META must cover cluster_id 0..k-1")
    frame = clustering_frame(style, spec, from_season=from_season)
    assigned, centers, _, feats = _fit_kmeans(frame, spec, k)
    counts = assigned.groupby("cluster_id").size().rename("n_player_seasons")
    dim = centers.merge(counts, on="cluster_id", how="left")
    dim["label"] = dim["cluster_id"].map(lambda i: CLUSTER_META[int(i)][0])
    dim["description"] = dim["cluster_id"].map(lambda i: CLUSTER_META[int(i)][1])
    dim["note"] = CLUSTER_NOTES
    dim["n_player_seasons"] = _to_int(dim["n_player_seasons"])
    dim["cluster_id"] = _to_int(dim["cluster_id"])
    dim = dim[
        ["cluster_id", "label", "description", "n_player_seasons", "note"]
        + [c for c in feats]
    ]
    fact = assigned[
        [
            "season",
            "player_code",
            "cluster_id",
            "distance",
            "second_cluster_id",
            "second_distance",
        ]
    ].copy()
    fact["season"] = fact["season"].astype(str)
    fact["player_code"] = _to_int(fact["player_code"])
    fact["cluster_id"] = _to_int(fact["cluster_id"])
    fact["second_cluster_id"] = _to_int(fact["second_cluster_id"])
    fact["distance"] = _num(fact["distance"])
    fact["second_distance"] = _num(fact["second_distance"])
    return dim, fact


def print_style_coverage(style: pd.DataFrame, spec: pd.DataFrame | None = None) -> None:
    if spec is None:
        spec = load_style_features()
    if style.empty:
        print("fact_player_style: (empty)", flush=True)
        return
    features = [str(n) for n in spec["name"]]
    rows = []
    for season, g in style.groupby("season", sort=True):
        rec = {"season": season, "n": len(g)}
        rec["gk"] = int((_to_int(g["element_type"]) == 1).sum())
        rec["pass_floor"] = int(g["fwd_pass_share"].notna().sum())
        rec["touch_floor"] = int(g["take_on_rate"].notna().sum())
        rec["both_floors"] = int(
            (g["fwd_pass_share"].notna() & g["take_on_rate"].notna()).sum()
        )
        rec["clusterable"] = int(
            clustering_frame(g, spec, from_season="2016-17").shape[0]
        )
        rec["single_club"] = int((_to_int(g["spell_count"]) == 1).sum())
        rec["movers"] = int((_to_int(g["spell_count"]) > 1).sum())
        for name in features:
            rec[name] = int(g[name].notna().sum())
        rows.append(rec)
    cov = pd.DataFrame(rows)
    core = cov[
        [
            "season",
            "n",
            "gk",
            "single_club",
            "movers",
            "pass_floor",
            "touch_floor",
            "both_floors",
            "clusterable",
        ]
    ]
    print("\n== fact_player_style coverage ==", flush=True)
    print(core.to_string(index=False), flush=True)
    print("\n== non-null features per season ==", flush=True)
    feat_cols = ["season"] + features
    print(cov[feat_cols].to_string(index=False), flush=True)


def _pos_label(et) -> str:
    if pd.isna(et):
        return "?"
    return POS_LABEL.get(int(et), str(int(et)))


def print_k_sweep(
    style: pd.DataFrame,
    dim_player: pd.DataFrame,
    spec: pd.DataFrame | None = None,
    *,
    ks: range = K_RANGE,
    from_season: str = FIT_FROM,
) -> None:
    try:
        import sys

        enc = (getattr(sys.stdout, "encoding", None) or "").lower().replace("-", "")
        if enc not in {"utf8"}:
            sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    if spec is None:
        spec = load_style_features()
    names = dim_player.copy()
    names["player_code"] = _to_int(names["code"] if "code" in names.columns else names["player_code"])
    name_col = "web_name" if "web_name" in names.columns else None
    names = names[["player_code"] + ([name_col] if name_col else [])].drop_duplicates(
        subset=["player_code"], keep="last"
    )

    frame = clustering_frame(style, spec, from_season=from_season)
    print(
        f"\n== k-means pool: {len(frame)} outfield player-seasons "
        f"from {from_season} with complete clustering z-vector "
        f"(GKs excluded, not fitted separately) ==",
        flush=True,
    )
    by_season = (
        frame.groupby("season").size().rename("n").reset_index()
        if not frame.empty
        else pd.DataFrame(columns=["season", "n"])
    )
    print(by_season.to_string(index=False), flush=True)
    if frame.empty:
        print("no clusterable rows", flush=True)
        return

    for k in ks:
        assigned, centers, dist, feats = _fit_kmeans(frame, spec, k)
        labeled = assigned.merge(names, on="player_code", how="left")
        print(f"\n######## k = {k} ########", flush=True)
        for cid in range(k):
            row = centers.loc[centers["cluster_id"] == cid].iloc[0]
            n = int((assigned["cluster_id"] == cid).sum())
            scores = {f: float(row[f]) for f in feats}
            ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
            top = ranked[:5]
            bot = ranked[-5:]
            print(f"\n-- cluster {cid}  n={n} --", flush=True)
            print(
                "  top 5  "
                + "  ".join(f"{name}={val:+.2f}" for name, val in top),
                flush=True,
            )
            print(
                "  bot 5  "
                + "  ".join(f"{name}={val:+.2f}" for name, val in bot),
                flush=True,
            )
            members = np.where(assigned["cluster_id"].to_numpy() == cid)[0]
            order = np.argsort(dist[members, cid])[:10]
            print("  closest:", flush=True)
            for i in members[order]:
                rec = labeled.iloc[int(i)]
                label = rec[name_col] if name_col else rec["player_code"]
                print(
                    f"    {rec['season']}  {str(label):<18}  {_pos_label(rec['element_type'])}  "
                    f"d={dist[int(i), cid]:.3f}",
                    flush=True,
                )


def _write_named_mart(name: str, df: pd.DataFrame) -> None:
    MARTS.mkdir(parents=True, exist_ok=True)
    dest = MARTS / f"{name}.parquet"
    df.to_parquet(dest, index=False)
    import duckdb

    con = duckdb.connect(str(DB_PATH))
    try:
        con.register("_df", df)
        con.execute(f'CREATE OR REPLACE TABLE "{name}" AS SELECT * FROM _df')
        con.unregister("_df")
    finally:
        con.close()
    print(f"wrote {dest} and table {name}", flush=True)


def write_style_mart(style: pd.DataFrame) -> None:
    _write_named_mart("fact_player_style", style)


def write_cluster_marts(dim: pd.DataFrame, fact: pd.DataFrame) -> None:
    _write_named_mart("dim_style_cluster", dim)
    _write_named_mart("fact_player_cluster", fact)


def main() -> None:
    import duckdb

    from transform.derived import season_element_type
    from transform.load import latest_bootstrap, load_players_raw

    opta_path = MARTS / "fact_player_season_opta.parquet"
    av_path = MARTS / "fact_player_season_availability.parquet"
    if not opta_path.exists():
        raise SystemExit("marts/fact_player_season_opta.parquet missing; run transform.build first")
    opta = pd.read_parquet(opta_path)
    availability = pd.read_parquet(av_path) if av_path.exists() else pd.DataFrame()
    _, bootstrap = latest_bootstrap()
    positions = season_element_type(load_players_raw(), bootstrap)
    spec = load_style_features()
    print("building fact_player_style...", flush=True)
    style = build_fact_player_style(opta, availability, positions, spec)
    print(f"  fact_player_style {len(style)}", flush=True)
    print_style_coverage(style, spec)
    write_style_mart(style)
    print(f"fitting k={CHOSEN_K} on the complete-case pool...", flush=True)
    dim, fact = build_cluster_tables(style, spec, k=CHOSEN_K)
    print(f"  fact_player_cluster {len(fact)}", flush=True)
    print(
        dim[["cluster_id", "label", "n_player_seasons"]].to_string(index=False),
        flush=True,
    )
    write_cluster_marts(dim, fact)
    con = duckdb.connect(str(DB_PATH), read_only=True)
    try:
        from transform.validate import _validate_clusters, _validate_style

        errs: list[str] = []
        _validate_style(con, errs)
        _validate_clusters(con, errs)
        if errs:
            from transform.validate import MartValidationError

            raise MartValidationError("style validation failed:\n- " + "\n- ".join(errs))
        print("style validation passed", flush=True)
    finally:
        con.close()


if __name__ == "__main__":
    main()
