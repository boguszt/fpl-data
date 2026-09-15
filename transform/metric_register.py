"""Generate data/metric_register.csv from ingested marts. Never hand-edit the CSV."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from ingest.paths import DATA, MARTS, RAW, REPO_ROOT, SEASONS
from transform.derived import P90_METRICS, SHARE_METRICS
from transform.load import load_style_features
from transform.metric_copy import (
    ARCHIVE_DEFINITION,
    FPL_COPY,
    KNOWN_COVERAGE,
    OPTA_COPY,
    OPTA_COUNTS,
    OPTA_LOWER_BETTER,
    OPTA_RATIO_FORMULAS,
    OPTA_RATIOS,
    STYLE_CLUSTER_CAVEAT,
    STYLE_CLUSTER_DEF,
    STYLE_COPY,
    archive_direction,
    archive_group,
    opta_id,
    sentence_label,
)
REGISTER_COLUMNS = [
    "id",
    "label",
    "feed",
    "raw_field",
    "definition",
    "derived_from",
    "grain",
    "context",
    "canonical",
    "first_season",
    "last_season",
    "coverage_note",
    "caveat",
    "group",
    "direction",
    "tier",
    "used_in",
]

CURRENT_SEASON = SEASONS[-1]
POS_LABEL = {1: "GK", 2: "DEF", 3: "MID", 4: "FWD"}

# JSON keys in players_*.json / matchlogs that are the 25 default table metrics.
CORE_WEB = {
    "goals": "goals",
    "assists": "assists",
    "xg": "xg",
    "xa": "xa",
    "xgi": "xgi",
    "tackles": "tackles",
    "recoveries": "recoveries",
    "cbi": "clearances_blocks_interceptions",
    "defcon": "defensive_contribution",
    "saves": "saves",
    "gc": "goals_conceded",
    "xgc": "xgc",
    "cs": "clean_sheets",
    "pensaved": "penalties_saved",
    "points": "total_points",
    "bps": "bps",
    "bonus": "bonus",
    "influence": "influence",
    "creativity": "creativity",
    "threat": "threat",
    "ict": "ict_index",
    "yellow": "yellow_cards",
    "red": "red_cards",
    "og": "own_goals",
    "penmissed": "penalties_missed",
}

# Same football action, different feeds. Flag if both canonical at one (grain, context).
CONCEPT_ALIASES = {
    "tackles": ("tackles", "o_total_tackle", "o_won_tackle"),
    "recoveries": ("recoveries", "o_ball_recovery"),
    "goals": ("goals", "o_goals"),
    "assists": ("assists", "o_goal_assist"),
    "clearances": (
        "clearances_blocks_interceptions",
        "o_effective_clearance",
        "o_total_clearance",
    ),
    "interceptions": ("o_interception", "o_interception_won"),
    "saves": ("saves", "o_saves"),
    "clean_sheets": ("clean_sheets", "o_clean_sheet"),
    "goals_conceded": ("goals_conceded", "o_goals_conceded"),
    "yellow_cards": ("yellow_cards", "o_yellow_card"),
    "red_cards": ("red_cards", "o_red_card"),
    "own_goals": ("own_goals", "o_own_goals"),
}

FPL_FEEDS = {"fpl", "vaastav", "fplcache"}
WEB_DATA = REPO_ROOT / "web" / "data"

IDENTITY_KEYS = {
    "player_code",
    "web_name",
    "first_name",
    "second_name",
    "team_name",
    "position",
    "nationality",
    "birth_date",
    "moved",
    "gw",
    "fixture",
    "blank",
    "date",
    "opp",
    "home",
    "team_score",
    "opp_score",
    "started",
    "features",
    "notes",
    "clusters",
    "cluster_id",
    "label",
    "description",
    "n",
    "centroid",
    "z",
    "raw",
    "c",
    "d",
    "c2",
    "d2",
    "generated_at",
    "seasons",
    "rows",
    "explain",
    "season",
    "start",
    "own",
    "end",
    "opta",
    "features_meta",
    "format",
    "pool_n",
    "name",
}

LIVE_VASTAV_NOTE = (
    "vaastav merged_gw carries FPL GW stats when no live file exists; "
    "event/{gw}/live/ wins for a (season, gw) that has a file"
)

FPL_RAW_LIVE = {
    "xg": "event.live.stats.expected_goals",
    "xa": "event.live.stats.expected_assists",
    "xgi": "event.live.stats.expected_goal_involvements",
    "xgc": "event.live.stats.expected_goals_conceded",
    "goals": "event.live.stats.goals_scored",
    "yellow_cards": "event.live.stats.yellow_cards",
    "red_cards": "event.live.stats.red_cards",
    "ict_index": "event.live.stats.ict_index",
    "total_points": "event.live.stats.total_points",
}


def _parquet(name: str) -> Path:
    return MARTS / f"{name}.parquet"


def _coverage(df: pd.DataFrame, col: str, season_col: str = "season") -> tuple[str | None, str | None, str]:
    if df is None or df.empty or col not in df.columns:
        return None, None, ""
    seasons = (
        df.loc[df[col].notna(), season_col].astype(str).drop_duplicates().sort_values()
    )
    if seasons.empty:
        return None, None, ""
    first, last = str(seasons.iloc[0]), str(seasons.iloc[-1])
    expected = [s for s in SEASONS if first <= s <= last]
    missing = [s for s in expected if s not in set(seasons)]
    gap = f"absent {', '.join(missing)}" if missing else ""
    return first, last, gap


def _used(*parts: str) -> str:
    seen: list[str] = []
    for p in parts:
        if p and p not in seen:
            seen.append(p)
    return ",".join(seen) if seen else "none"


def _last(last: str | None) -> str:
    if not last:
        return ""
    return "" if str(last) == CURRENT_SEASON else str(last)


def _coverage_note(mid: str, gap: str = "", extra: str = "") -> str:
    bits: list[str] = []
    alts = [mid]
    if mid.startswith("o_"):
        alts.append(mid[2:])
    else:
        alts.append(f"o_{mid}")
    known = next((KNOWN_COVERAGE[k] for k in alts if k in KNOWN_COVERAGE), None)
    if known:
        bits.append(known)
    elif gap:
        bits.append(gap)
    if extra:
        bits.append(extra)
    return " ".join(bits)


def _row(**kwargs) -> dict:
    rec = {c: kwargs.get(c, "") for c in REGISTER_COLUMNS}
    rec["canonical"] = int(kwargs.get("canonical", 0))
    rec["direction"] = int(kwargs.get("direction", 1) or 1)
    rec["first_season"] = kwargs.get("first_season") or ""
    rec["last_season"] = _last(kwargs.get("last_season") or "")
    rec["derived_from"] = kwargs.get("derived_from") or ""
    rec["definition"] = kwargs.get("definition") or ""
    rec["coverage_note"] = kwargs.get("coverage_note") or ""
    rec["caveat"] = kwargs.get("caveat") or ""
    rec["group"] = kwargs.get("group") or ""
    rec["used_in"] = kwargs.get("used_in") or "none"
    rec["tier"] = kwargs.get("tier") or "archive"
    rec["context"] = kwargs.get("context") or "football"
    rec["feed"] = kwargs.get("feed") or ""
    rec["raw_field"] = kwargs.get("raw_field") or ""
    rec["label"] = kwargs.get("label") or kwargs.get("id")
    rec["grain"] = kwargs.get("grain") or "season"
    rec["id"] = kwargs.get("id")
    return rec


def _read(name: str, columns: list[str] | None = None) -> pd.DataFrame:
    path = _parquet(name)
    if not path.exists():
        return pd.DataFrame()
    if columns is None:
        return pd.read_parquet(path)
    import pyarrow.parquet as pq

    names = set(pq.read_schema(path).names)
    cols = [c for c in columns if c in names]
    if "season" in names and "season" not in cols:
        cols = ["season"] + cols
    if not cols:
        return pd.DataFrame()
    return pd.read_parquet(path, columns=cols)


def _fpl_copy(mid: str) -> tuple[str, str, str, str, int]:
    if mid in FPL_COPY:
        return FPL_COPY[mid]
    return sentence_label(mid), "", "", "Fantasy", 1


def _shrinkage_k_text() -> str:
    path = _parquet("dim_shrinkage")
    if not path.exists():
        return (
            "dim_shrinkage was not available when this register was built. "
            "The marts fall back to k=10; the frontend also has its own k constants."
        )
    df = pd.read_parquet(path)
    if df.empty or "k" not in df.columns:
        return (
            "dim_shrinkage is empty. The marts fall back to k=10; "
            "the frontend also has its own k constants."
        )
    latest = (
        df.sort_values("season")
        .groupby(["metric", "element_type"], as_index=False)
        .last()
    )
    parts: list[str] = []
    for metric, g in latest.groupby("metric"):
        bits: list[str] = []
        for rec in g.sort_values("element_type").to_dict("records"):
            pos = POS_LABEL.get(int(rec["element_type"]), str(int(rec["element_type"])))
            method = str(rec.get("method") or "").strip()
            try:
                k_val = float(rec["k"])
                k_s = str(int(k_val)) if k_val.is_integer() else f"{k_val:.1f}"
            except (TypeError, ValueError):
                k_s = str(rec["k"])
            bits.append(f"{pos} k={k_s}" + (f" ({method})" if method else ""))
        parts.append(f"{metric}: " + "; ".join(bits) + ".")
    return (
        "Fitted k from dim_shrinkage, constant across seasons for each "
        "(metric, position); the pool mean is still (season, position). "
        "The frontend also has its own k constants on METRICS. "
        + " ".join(parts)
    )


def build_metric_register() -> pd.DataFrame:
    gw_cols = [
        "season",
        "minutes",
        "starts",
        "goals",
        "assists",
        "expected_goals",
        "expected_assists",
        "expected_goal_involvements",
        "expected_goals_conceded",
        "tackles",
        "recoveries",
        "clearances_blocks_interceptions",
        "defensive_contribution",
        "saves",
        "goals_conceded",
        "clean_sheets",
        "penalties_saved",
        "penalties_missed",
        "total_points",
        "bps",
        "bonus",
        "influence",
        "creativity",
        "threat",
        "ict_index",
        "yellow",
        "red",
        "own_goals",
    ]
    gw = _read("fact_player_gw", gw_cols)
    style = _read("fact_player_style")
    opta = _read("fact_player_season_opta")
    snap = _read(
        "snap_player_day",
        [
            "season",
            "now_cost",
            "selected_by_percent",
            "transfers_in_event",
            "transfers_out_event",
            "status",
            "chance_of_playing_next_round",
            "news",
        ],
    )
    metrics = _read("fact_player_season_metrics")
    spec = load_style_features()
    rows: list[dict] = []
    unconfident: list[str] = []

    core_ids = set(CORE_WEB.values())
    style_inputs: set[str] = set()
    for _, r in spec.iterrows():
        for expr in (str(r["numerator"]), str(r["denominator"])):
            if expr in {"D", "team_total_pass", "team_total_scoring_att", "team_touches"}:
                continue
            for tok in expr.split("+"):
                tok = tok.strip()
                if tok:
                    style_inputs.add(tok)

    gw_col = {
        "xg": "expected_goals",
        "xa": "expected_assists",
        "xgi": "expected_goal_involvements",
        "xgc": "expected_goals_conceded",
        "yellow_cards": "yellow",
        "red_cards": "red",
        "ict_index": "ict_index",
    }
    football_fpl = {"xg", "xa", "xgi", "xgc"}
    playing_time = {"minutes", "starts"}

    for mid in core_ids | playing_time:
        col = gw_col.get(mid, mid)
        first, last, gap = _coverage(gw, col)
        if mid in football_fpl:
            ctx = "both"
        elif mid in playing_time:
            ctx = "both"
        else:
            ctx = "fantasy"
        label, definition, caveat, group, direction = _fpl_copy(mid)
        if not definition:
            unconfident.append(mid)
        extra = LIVE_VASTAV_NOTE
        used_gw = _used(
            "profile",
            "percentile" if mid in core_ids else "",
            "shrinkage" if mid in P90_METRICS else "",
        )
        used_season = _used(
            "table" if mid in core_ids else "table",
            "percentile" if mid in core_ids else "",
            "shrinkage" if mid in P90_METRICS else "",
        )
        raw_live = FPL_RAW_LIVE.get(mid, f"event.live.stats.{col}")
        rows.append(
            _row(
                id=mid,
                label=label,
                feed="fpl",
                raw_field=raw_live,
                definition=definition,
                grain="gameweek",
                context=ctx,
                canonical=1,
                first_season=first,
                last_season=last,
                coverage_note=_coverage_note(mid, gap, extra),
                caveat=caveat,
                group=group,
                direction=direction,
                tier="core" if mid in core_ids else "extended",
                used_in=used_gw,
            )
        )
        rows.append(
            _row(
                id=mid,
                label=label,
                feed="fpl",
                raw_field=f"sum(fact_player_gw.{col})",
                definition=definition,
                grain="season",
                context=ctx,
                canonical=1,
                first_season=first,
                last_season=last,
                coverage_note=_coverage_note(mid, gap, extra),
                caveat=caveat,
                group=group,
                direction=direction,
                tier="core" if mid in core_ids else "extended",
                used_in=used_season if used_season != "none" else "table",
            )
        )

    av = _read(
        "fact_player_season_availability",
        [
            "season",
            "minutes",
            "appearances",
            "starts",
            "appearances_60_plus",
            "team_minutes_available",
            "minutes_share",
        ],
    )
    for mid, col, derived in (
        ("appearances", "appearances", "count of matches with minutes > 0"),
        ("appearances_60_plus", "appearances_60_plus", "count of matches with minutes >= 60"),
        ("team_minutes_available", "team_minutes_available", "sum of club fixture minutes while at the club"),
        ("minutes_share", "minutes_share", "minutes / team_minutes_available"),
    ):
        first, last, gap = _coverage(av, col)
        label, definition, caveat, group, direction = _fpl_copy(mid)
        if not definition:
            unconfident.append(mid)
        rows.append(
            _row(
                id=mid,
                label=label,
                feed="derived",
                raw_field=f"fact_player_season_availability.{col}",
                definition=definition,
                derived_from=derived,
                grain="season",
                context="both",
                canonical=1,
                first_season=first,
                last_season=last,
                coverage_note=_coverage_note(mid, gap),
                caveat=caveat,
                group=group,
                direction=direction,
                tier="extended",
                used_in="table",
            )
        )

    first_p, last_p, gap_p = _coverage(snap, "now_cost")
    label, definition, caveat, group, direction = _fpl_copy("price")
    rows.append(
        _row(
            id="price",
            label=label,
            feed="fplcache",
            raw_field="snap_player_day.now_cost / 10",
            definition=definition,
            derived_from="now_cost / 10",
            grain="snapshot",
            context="fantasy",
            canonical=1,
            first_season=first_p,
            last_season=last_p,
            coverage_note=_coverage_note("price", gap_p),
            caveat=caveat,
            group=group,
            direction=direction,
            tier="extended",
            used_in="table,profile",
        )
    )
    first_o, last_o, gap_o = _coverage(snap, "selected_by_percent")
    label, definition, caveat, group, direction = _fpl_copy("ownership")
    rows.append(
        _row(
            id="ownership",
            label=label,
            feed="fplcache",
            raw_field="snap_player_day.selected_by_percent",
            definition=definition,
            grain="snapshot",
            context="fantasy",
            canonical=1,
            first_season=first_o,
            last_season=last_o,
            coverage_note=_coverage_note("ownership", gap_o),
            caveat=caveat,
            group=group,
            direction=direction,
            tier="extended",
            used_in="table",
        )
    )
    for col, snap_label in (
        ("transfers_in_event", "Transfers in (GW)"),
        ("transfers_out_event", "Transfers out (GW)"),
        ("status", "Availability status"),
        ("chance_of_playing_next_round", "Chance of playing next round"),
        ("news", "News"),
    ):
        first, last, _ = _coverage(snap, col)
        unconfident.append(col)
        rows.append(
            _row(
                id=col,
                label=snap_label,
                feed="fplcache",
                raw_field=f"bootstrap.elements.{col}",
                definition="Bootstrap snapshot field. Not exported to the player table.",
                grain="snapshot",
                context="fantasy",
                canonical=1,
                first_season=first,
                last_season=last,
                coverage_note="snap_player_day only. Never exported to web/data.",
                caveat="",
                group="Fantasy",
                direction=1,
                tier="archive",
                used_in="none",
            )
        )

    for m in P90_METRICS:
        parent = _fpl_copy(m)
        ctx = "both" if m in football_fpl else "fantasy"
        first, last, gap = _coverage(metrics, f"{m}_p90") if not metrics.empty else (None, None, "")
        rows.append(
            _row(
                id=f"{m}_p90",
                label=f"{parent[0]} per 90",
                feed="derived",
                raw_field=f"fact_player_season_metrics.{m}_p90",
                definition=(
                    f"The {parent[0].lower()} total divided by minutes and scaled to 90. "
                    "The frontend derives this itself from the season total and minutes; "
                    "the mart stores the same formula."
                ),
                derived_from=f"{m}_total / minutes_total × 90",
                grain="season",
                context=ctx,
                canonical=1,
                first_season=first,
                last_season=last,
                coverage_note=_coverage_note(m, gap),
                caveat=parent[2],
                group=parent[3],
                direction=parent[4],
                tier="extended",
                used_in="shrinkage",
            )
        )
        first, last, gap = _coverage(metrics, f"{m}_adj90") if not metrics.empty else (None, None, "")
        rows.append(
            _row(
                id=f"{m}_adj90",
                label=f"{parent[0]} adjusted per 90",
                feed="derived",
                raw_field=f"fact_player_season_metrics.{m}_adj90",
                definition=(
                    f"The adjusted per-90 of {parent[0].lower()}. See adjusted_per_90 "
                    "for the formula and the fitted k."
                ),
                derived_from=f"(total + k × pool_mean) / (nineties + k) on {m}",
                grain="season",
                context=ctx,
                canonical=1,
                first_season=first,
                last_season=last,
                coverage_note=_coverage_note(m, gap),
                caveat=parent[2],
                group=parent[3],
                direction=parent[4],
                tier="extended",
                used_in="shrinkage,percentile",
            )
        )
    for m in SHARE_METRICS:
        parent = _fpl_copy(m)
        ctx = "both" if m in football_fpl else "fantasy"
        first, last, gap = _coverage(metrics, f"{m}_share") if not metrics.empty else (None, None, "")
        rows.append(
            _row(
                id=f"{m}_share",
                label=f"{parent[0]} share of team",
                feed="derived",
                raw_field=f"fact_player_season_metrics.{m}_share",
                definition=(
                    f"This player's {parent[0].lower()} as a proportion of the club's "
                    "total in the same matches. See share_of_team."
                ),
                derived_from=f"player {m} / team {m} over GWs at the club",
                grain="season",
                context=ctx,
                canonical=1,
                first_season=first,
                last_season=last,
                coverage_note=_coverage_note("share_of_team", gap),
                caveat="NULL for players who changed club mid-season.",
                group=parent[3],
                direction=parent[4],
                tier="extended",
                used_in="percentile",
            )
        )

    # Style features (derived Opta ratios). Prefix o_. Skip the five Opta DERIVED
    # ratios — those ship from fact_player_season_opta without the style floors.
    for _, spec_row in spec.iterrows():
        name = str(spec_row["name"])
        if name in OPTA_RATIOS:
            continue
        oid = opta_id(name)
        first, last, gap = _coverage(style, name)
        in_cl = int(spec_row["in_clustering"] or 0) == 1
        if name in STYLE_COPY:
            label, definition, caveat, group = STYLE_COPY[name]
        else:
            label, definition, caveat, group = sentence_label(name), "", "", "Passing"
            unconfident.append(oid)
        extra = ""
        if not in_cl:
            extra = "Excluded from clustering because it measures quality, not shape."
        rows.append(
            _row(
                id=oid,
                label=label,
                feed="derived",
                raw_field=f"plstats.{spec_row['numerator']} / plstats.{spec_row['denominator']}",
                definition=definition,
                derived_from=f"{spec_row['numerator']} / {spec_row['denominator']}",
                grain="season",
                context="football",
                canonical=1,
                first_season=first,
                last_season=last,
                coverage_note=_coverage_note(oid, gap, extra),
                caveat=caveat,
                group=group,
                direction=-1 if "loss" in name else 1,
                tier="extended",
                used_in="table,clustering" if in_cl else "table",
            )
        )

    shipped_opta = set(OPTA_COUNTS) | set(OPTA_RATIOS)
    skip_opta = {"season", "player_code", "pulse_id"}
    if not opta.empty:
        for col, group in OPTA_COUNTS.items():
            first, last, gap = _coverage(opta, col)
            oid = opta_id(col)
            if col in OPTA_COPY:
                label, definition, caveat = OPTA_COPY[col]
            else:
                label, definition, caveat = sentence_label(col), "", ""
                unconfident.append(oid)
            used = "table,clustering" if col in style_inputs else "table"
            extra = "Pulse season totals. Verbatim API; omitted zeros stay NULL."
            rows.append(
                _row(
                    id=oid,
                    label=label,
                    feed="opta",
                    raw_field=f"plstats.{col}",
                    definition=definition,
                    grain="season",
                    context="football",
                    canonical=1,
                    first_season=first,
                    last_season=last,
                    coverage_note=_coverage_note(oid, gap, extra),
                    caveat=caveat,
                    group=group,
                    direction=-1 if col in OPTA_LOWER_BETTER else 1,
                    tier="extended",
                    used_in=used,
                )
            )
        for col, group in OPTA_RATIOS.items():
            first, last, gap = _coverage(opta, col)
            oid = opta_id(col)
            if col in OPTA_COPY:
                label, definition, caveat = OPTA_COPY[col]
            else:
                label, definition, caveat = sentence_label(col), "", ""
                unconfident.append(oid)
            extra = "NULL when the denominator is zero. Pulse season grain only."
            rows.append(
                _row(
                    id=oid,
                    label=label,
                    feed="opta",
                    raw_field=f"plstats.{OPTA_RATIO_FORMULAS[col].split('/')[0].strip()} / …",
                    definition=definition,
                    derived_from=OPTA_RATIO_FORMULAS[col],
                    grain="season",
                    context="football",
                    canonical=1,
                    first_season=first,
                    last_season=last,
                    coverage_note=_coverage_note(oid, gap, extra),
                    caveat=caveat,
                    group=group,
                    direction=1,
                    tier="extended",
                    used_in="table",
                )
            )
        for col in opta.columns:
            if col in skip_opta or col in shipped_opta:
                continue
            first, last, gap = _coverage(opta, col)
            oid = opta_id(col)
            unconfident.append(oid)
            extra = "Pulse season totals. Verbatim API; omitted zeros stay NULL. Not exported to web/data."
            rows.append(
                _row(
                    id=oid,
                    label=sentence_label(col),
                    feed="opta",
                    raw_field=f"plstats.{col}",
                    definition=ARCHIVE_DEFINITION,
                    grain="season",
                    context="football",
                    canonical=1,
                    first_season=first,
                    last_season=last,
                    coverage_note=_coverage_note(oid, gap, extra),
                    caveat="",
                    group=archive_group(col),
                    direction=archive_direction(col),
                    tier="archive",
                    used_in="clustering" if col in style_inputs else "none",
                )
            )

    from transform.explain import explain_labels

    labels = explain_labels()
    live_first, live_last = _live_season_span()
    live_extra = (
        "Points breakdown per fixture from event/{gw}/live/ explain[]. "
        "Omitted from matchlogs when the GW has no live file. "
        "Every component FPL returned is kept, including zero-point minutes."
    )
    for ident, expl_label in labels.items():
        mapped = {
            "goals_scored": "goals",
            "yellow_cards": "yellow_cards",
            "red_cards": "red_cards",
            "ict_index": "ict_index",
            "expected_goals": "xg",
            "expected_assists": "xa",
            "expected_goal_involvements": "xgi",
            "expected_goals_conceded": "xgc",
        }.get(ident, ident)
        if ident in {
            "influence",
            "creativity",
            "threat",
            "ict_index",
            "expected_goals",
            "expected_assists",
            "expected_goal_involvements",
            "expected_goals_conceded",
            "bps",
            "starts",
            "tackles",
            "recoveries",
            "clearances_blocks_interceptions",
        }:
            continue
        label, definition, caveat, group, direction = _fpl_copy(mapped)
        if not definition:
            definition = f"FPL explain component labelled '{expl_label}'."
            unconfident.append(f"{mapped}@fixture")
        rows.append(
            _row(
                id=mapped,
                label=label if mapped in FPL_COPY else expl_label,
                feed="fpl",
                raw_field=f"event.live.explain[].stats.{ident}",
                definition=definition,
                grain="fixture",
                context="fantasy",
                canonical=1,
                first_season=live_first,
                last_season=live_last,
                coverage_note=_coverage_note(mapped, "", live_extra),
                caveat=caveat,
                group=group,
                direction=direction,
                tier="core" if mapped in core_ids else "extended",
                used_in="profile",
            )
        )

    k_text = _shrinkage_k_text()
    rows.append(
        _row(
            id="adjusted_per_90",
            label="Adjusted per 90",
            feed="derived",
            raw_field="fact_player_season_metrics.{metric}_adj90",
            definition=(
                "The per-90 rate pulled toward the positional average by an amount set "
                "by how few minutes the player has. A full season barely moves; a few "
                "hundred minutes moves a long way. "
                + k_text
            ),
            derived_from="(total + k × pool_mean) / (nineties + k)",
            grain="season",
            context="both",
            canonical=1,
            first_season=SEASONS[0],
            last_season=CURRENT_SEASON,
            coverage_note=(
                "Fitted on FPL counting stats only. Opta season totals are not shrunk "
                "in the marts; the frontend may apply its own k if it rates them."
            ),
            caveat=(
                "k is a sample-size weight, not a quality judgement. Extreme fallback "
                "values (own goals, reds) mean the split-half had almost no signal — "
                "read those adjusted rates as 'almost the positional mean'."
            ),
            group="Fantasy",
            direction=1,
            tier="extended",
            used_in="shrinkage,percentile",
        )
    )
    rows.append(
        _row(
            id="percentile",
            label="Percentile",
            feed="derived",
            raw_field="frontend rank of adjusted_per_90",
            definition=(
                "Rank position expressed 0–100 against same-position players clearing "
                "the minutes floor, on the adjusted rate."
            ),
            derived_from="percentile rank of adjusted_per_90 within (season, position) after the minutes floor",
            grain="season",
            context="both",
            canonical=1,
            first_season=SEASONS[0],
            last_season=CURRENT_SEASON,
            coverage_note="Computed in the frontend against the current filtered set, not stored in the marts.",
            caveat=(
                "A 90th percentile in a 12-player filter is not the same claim as in "
                "the whole league. Direction is already applied: 100 is always 'good'."
            ),
            group="Fantasy",
            direction=1,
            tier="extended",
            used_in="percentile",
        )
    )
    rows.append(
        _row(
            id="share_of_team",
            label="Share of team",
            feed="derived",
            raw_field="fact_player_season_metrics.{metric}_share",
            definition=(
                "The player's output as a proportion of their club's total in the same "
                "matches. NULL for players who changed club mid-season."
            ),
            derived_from="player total / club total over the gameweeks the player was registered at that club",
            grain="season",
            context="both",
            canonical=1,
            first_season="2020-21",
            last_season=CURRENT_SEASON,
            coverage_note=_coverage_note("share_of_team"),
            caveat=(
                "A mid-season move cannot be split on the Pulse row either: Opta team "
                "shares are also NULL for movers. Do not approximate."
            ),
            group="Fantasy",
            direction=1,
            tier="extended",
            used_in="percentile",
        )
    )
    cluster_first, cluster_last, cluster_gap = _coverage(
        _read("fact_player_cluster", ["season", "cluster_id"]), "cluster_id"
    )
    rows.append(
        _row(
            id="style_cluster",
            label="Style cluster",
            feed="derived",
            raw_field="fact_player_cluster.cluster_id",
            definition=STYLE_CLUSTER_DEF,
            derived_from="k-means (k=9) on z-scored style ratios, fit once on complete outfield cases",
            grain="season",
            context="football",
            canonical=1,
            first_season=cluster_first,
            last_season=cluster_last,
            coverage_note=_coverage_note(
                "style_cluster",
                cluster_gap,
                "Keepers and managers are excluded. Carries are not league-wide until 2024-25, so most assignments sit in 2024-25 onward.",
            ),
            caveat=STYLE_CLUSTER_CAVEAT,
            group="Territory",
            direction=1,
            tier="extended",
            used_in="clustering",
        )
    )

    out = pd.DataFrame(rows, columns=REGISTER_COLUMNS)
    out["canonical"] = pd.to_numeric(out["canonical"], errors="coerce").fillna(0).astype(int)
    out["direction"] = pd.to_numeric(out["direction"], errors="coerce").fillna(1).astype(int)
    out = out.drop_duplicates(subset=["id", "grain", "context", "feed"], keep="first")
    out = out.sort_values(["tier", "grain", "id", "context"]).reset_index(drop=True)
    out.attrs["unconfident"] = sorted(set(unconfident))
    return out


def _live_season_span() -> tuple[str, str]:
    root = RAW / "live"
    if not root.exists():
        return "", ""
    seasons = sorted(p.name for p in root.iterdir() if p.is_dir())
    if not seasons:
        return "", ""
    return seasons[0], seasons[-1]


def write_metric_register(df: pd.DataFrame | None = None) -> Path:
    if df is None:
        df = build_metric_register()
    DATA.mkdir(parents=True, exist_ok=True)
    dest = DATA / "metric_register.csv"
    df.to_csv(dest, index=False)
    print(f"wrote {dest}  {len(df)} rows", flush=True)
    return dest


def write_metrics_register_json(df: pd.DataFrame, dest: Path | None = None) -> Path:
    dest = dest or (WEB_DATA / "metrics_register.json")
    dest.parent.mkdir(parents=True, exist_ok=True)
    payload = df.to_dict(orient="records")
    for rec in payload:
        rec["canonical"] = int(rec["canonical"])
        rec["direction"] = int(rec.get("direction") or 1)
        for k in (
            "first_season",
            "last_season",
            "derived_from",
            "definition",
            "coverage_note",
            "caveat",
            "group",
            "used_in",
        ):
            if rec.get(k) is None:
                rec[k] = ""
    encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    dest.write_text(encoded, encoding="utf-8")
    print(f"wrote {dest}  {len(encoded):,} bytes  {len(payload)} rows", flush=True)
    return dest


def flag_dual_canonical(df: pd.DataFrame) -> list[str]:
    """FPL-family and Opta both canonical at the same grain and context."""
    flags: list[str] = []
    id_to_concept: dict[str, str] = {}
    for concept, names in CONCEPT_ALIASES.items():
        for n in names:
            id_to_concept[n] = concept
    work = df.loc[df["canonical"] == 1].copy()
    work["concept"] = work["id"].map(lambda i: id_to_concept.get(str(i), str(i)))
    work["family"] = work["feed"].map(
        lambda f: "opta" if f == "opta" else ("fpl" if f in FPL_FEEDS else f)
    )
    for (grain, context, concept), g in work.groupby(["grain", "context", "concept"], dropna=False):
        families = set(g["family"])
        if "fpl" in families and "opta" in families:
            ids = ", ".join(sorted(set(g["id"].astype(str))))
            flags.append(
                f"DUAL CANONICAL {concept} at grain={grain} context={context}: {ids}"
            )
    for (mid, grain, context), g in work.groupby(["id", "grain", "context"], dropna=False):
        feeds = sorted(set(g["feed"].astype(str)))
        if len(feeds) > 1:
            flags.append(
                f"DUAL CANONICAL id={mid} grain={grain} context={context} feeds={feeds}"
            )
    return flags


def flag_key_collisions(df: pd.DataFrame) -> list[str]:
    """Opta ids must be o_-prefixed and must not equal an FPL-family id."""
    flags: list[str] = []
    opta = df.loc[df["feed"] == "opta", "id"].astype(str)
    bare = [i for i in opta if not i.startswith("o_")]
    if bare:
        flags.append("opta ids missing o_ prefix: " + ", ".join(sorted(set(bare))[:20]))
    fpl_ids = set(df.loc[df["feed"].isin(FPL_FEEDS | {"fpl"}), "id"].astype(str))
    overlap = sorted(set(opta) & fpl_ids)
    if overlap:
        flags.append("feed key collision: " + ", ".join(overlap[:20]))
    return flags


def web_metric_keys(web_dir: Path | None = None) -> set[str]:
    web_dir = web_dir or WEB_DATA
    keys: set[str] = set()

    def walk(obj, parent: str | None = None):
        if isinstance(obj, dict):
            for k, v in obj.items():
                if str(k).isdigit():
                    walk(v, k)
                    continue
                if k in IDENTITY_KEYS:
                    if k == "explain" and isinstance(v, list):
                        for item in v:
                            if isinstance(item, dict) and item.get("identifier"):
                                keys.add(str(item["identifier"]))
                    elif k == "features" and isinstance(v, list):
                        for feat in v:
                            keys.add(str(feat))
                    elif k == "features_meta":
                        continue
                    else:
                        walk(v, k)
                    continue
                keys.add(str(k))
                walk(v, k)
        elif isinstance(obj, list):
            for item in obj:
                walk(item, parent)

    for path in sorted(web_dir.glob("*.json")):
        if path.name in {"metrics_register.json", "status.json"}:
            continue
        if path.name.startswith("teams_") or path.name.startswith("fixtures_"):
            continue
        walk(json.loads(path.read_text(encoding="utf-8")))
    return keys


def json_key_to_id(key: str) -> str | None:
    if key in IDENTITY_KEYS:
        return None
    if key.endswith("_total"):
        base = key[: -len("_total")]
        return CORE_WEB.get(base, base)
    if key.endswith("_team"):
        base = key[: -len("_team")]
        if base in CORE_WEB:
            return CORE_WEB[base]
    if key in CORE_WEB:
        return CORE_WEB[key]
    if key in {"price_now", "price_start", "price_delta", "price"}:
        return "price"
    if key in {"own_now", "own_7d", "own_30d"}:
        return "ownership"
    if key == "goals_scored":
        return "goals"
    if key == "apps_60":
        return "appearances_60_plus"
    if key == "team_minutes":
        return "team_minutes_available"
    if key in STYLE_COPY or key in OPTA_COUNTS or key in OPTA_RATIOS:
        return opta_id(key)
    return key


def validate_metric_register(df: pd.DataFrame, web_dir: Path | None = None) -> list[str]:
    errors: list[str] = []
    errors.extend(flag_dual_canonical(df))
    for f in flag_key_collisions(df):
        if f.startswith("o_ prefix is the only thing"):
            continue
        errors.append(f)

    id_only = set(df["id"].astype(str))
    web_keys = web_metric_keys(web_dir)
    missing: list[str] = []
    for key in sorted(web_keys):
        mid = json_key_to_id(key)
        if mid is None:
            continue
        if mid not in id_only:
            missing.append(key)
    if missing:
        errors.append("web/data keys with no register id: " + ", ".join(missing[:40]))

    archive_ids = set(df.loc[df["tier"] == "archive", "id"].astype(str))
    shipped_ids = set(df.loc[df["tier"] != "archive", "id"].astype(str))
    archive_only = archive_ids - shipped_ids
    leaked = [k for k in web_keys if json_key_to_id(k) in archive_only]
    if leaked:
        errors.append("archive ids exported as web/data keys: " + ", ".join(sorted(leaked)[:40]))

    shipped = df.loc[df["tier"] != "archive"]
    blank_def = shipped.loc[shipped["definition"].astype(str).str.strip() == ""]
    if not blank_def.empty:
        ids = ", ".join(sorted(set(blank_def["id"].astype(str)))[:40])
        errors.append(f"shipped metrics with empty definition: {ids}")
    blank_label = shipped.loc[shipped["label"].astype(str).str.strip() == ""]
    if not blank_label.empty:
        ids = ", ".join(sorted(set(blank_label["id"].astype(str)))[:20])
        errors.append(f"shipped metrics with empty label: {ids}")
    blank_group = shipped.loc[shipped["group"].astype(str).str.strip() == ""]
    if not blank_group.empty:
        ids = ", ".join(sorted(set(blank_group["id"].astype(str)))[:20])
        errors.append(f"shipped metrics with empty group: {ids}")

    caveat_required = set(FPL_COPY) | set(OPTA_COPY) | {
        "adjusted_per_90",
        "percentile",
        "share_of_team",
        "style_cluster",
    }
    for mid in sorted(caveat_required):
        hit = shipped.loc[shipped["id"].astype(str).isin({mid, opta_id(mid)})]
        if hit.empty:
            continue
        if (hit["caveat"].astype(str).str.strip() == "").all() and mid in FPL_COPY:
            _, _, caveat, _, _ = FPL_COPY[mid]
            if caveat:
                errors.append(f"missing caveat for {mid}")

    bad_first = df.loc[df["first_season"].astype(str).str.len() > 0]
    for rec in bad_first.to_dict("records"):
        fs = str(rec["first_season"])
        if len(fs) != 7 or fs[4] != "-":
            errors.append(f"bad first_season for {rec['id']}: {fs!r}")

    current = df.loc[df["last_season"].astype(str) == CURRENT_SEASON]
    if not current.empty:
        ids = ", ".join(sorted(set(current["id"].astype(str)))[:20])
        errors.append(f"last_season still set to current season {CURRENT_SEASON}: {ids}")

    opta_shipped = set(df.loc[(df["feed"] == "opta") & (df["tier"] != "archive"), "id"].astype(str))
    expected = {opta_id(c) for c in list(OPTA_COUNTS) + list(OPTA_RATIOS)}
    missing_opta = sorted(expected - opta_shipped)
    if missing_opta:
        errors.append("shipped Opta ids missing from register: " + ", ".join(missing_opta[:20]))
    return errors


def print_tier_counts(df: pd.DataFrame) -> None:
    print("\n== metric register tiers ==", flush=True)
    print(df.groupby("tier").size().rename("n_rows").to_string(), flush=True)
    print("\nunique ids per tier:", flush=True)
    print(df.groupby("tier")["id"].nunique().rename("n_ids").to_string(), flush=True)
    print("\nby tier, grain:", flush=True)
    print(df.groupby(["tier", "grain"]).size().rename("n").to_string(), flush=True)
    unconf = df.attrs.get("unconfident") or []
    print(f"\nunconfident definition/caveat: {len(unconf)}", flush=True)
    if unconf:
        preview = ", ".join(unconf[:30])
        more = f" (+{len(unconf) - 30} more)" if len(unconf) > 30 else ""
        print(f"  {preview}{more}", flush=True)


def main() -> None:
    df = build_metric_register()
    write_metric_register(df)
    print_tier_counts(df)
    flags = flag_dual_canonical(df)
    if flags:
        print("\nDUAL-CANONICAL FLAGS:", flush=True)
        for f in flags:
            print(f"  {f}", flush=True)
    else:
        print("\nno FPL+Opta dual-canonical at the same grain and context", flush=True)
    for f in flag_key_collisions(df):
        print(f"  {f}", flush=True)


if __name__ == "__main__":
    main()
