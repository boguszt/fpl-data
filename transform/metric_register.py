"""Generate data/metric_register.csv from ingested marts. Never hand-edit the CSV."""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from ingest.paths import DATA, MARTS, RAW, REPO_ROOT, SEASONS
from transform.derived import P90_METRICS, SHARE_METRICS
from transform.load import load_style_features
from transform.opta import DERIVED as OPTA_DERIVED

REGISTER_COLUMNS = [
    "id",
    "label",
    "feed",
    "raw_field",
    "grain",
    "context",
    "canonical",
    "first_season",
    "last_season",
    "tier",
    "used_in",
    "derived_from",
    "note",
]

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

FPL_LABELS = {
    "goals": "Goals",
    "assists": "Assists",
    "xg": "Expected goals",
    "xa": "Expected assists",
    "xgi": "Expected goal involvements",
    "xgc": "Expected goals conceded",
    "tackles": "Tackles",
    "recoveries": "Recoveries",
    "clearances_blocks_interceptions": "Clearances / blocks / interceptions",
    "defensive_contribution": "Defensive contribution",
    "saves": "Saves",
    "goals_conceded": "Goals conceded",
    "clean_sheets": "Clean sheets",
    "penalties_saved": "Penalties saved",
    "penalties_missed": "Penalties missed",
    "total_points": "Total points",
    "bps": "Bonus point system",
    "bonus": "Bonus",
    "influence": "Influence",
    "creativity": "Creativity",
    "threat": "Threat",
    "ict_index": "ICT Index",
    "yellow_cards": "Yellow cards",
    "red_cards": "Red cards",
    "own_goals": "Own goals",
    "minutes": "Minutes",
    "starts": "Starts",
    "appearances": "Appearances",
    "appearances_60_plus": "Appearances 60+",
    "team_minutes_available": "Team minutes available",
    "minutes_share": "Minutes share of team",
    "price": "Price",
    "ownership": "Ownership",
}

# Same football action, different feeds. Flag if both canonical at one (grain, context).
CONCEPT_ALIASES = {
    "tackles": ("tackles", "total_tackle", "won_tackle"),
    "recoveries": ("recoveries", "ball_recovery"),
    "goals": ("goals", "goals_scored"),
    "assists": ("assists", "goal_assist"),
    "clearances": (
        "clearances_blocks_interceptions",
        "effective_clearance",
        "total_clearance",
    ),
    "interceptions": ("interception", "interception_won"),
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


def _row(**kwargs) -> dict:
    rec = {c: kwargs.get(c, "") for c in REGISTER_COLUMNS}
    rec["canonical"] = int(kwargs.get("canonical", 0))
    rec["first_season"] = kwargs.get("first_season") or ""
    rec["last_season"] = kwargs.get("last_season") or ""
    rec["derived_from"] = kwargs.get("derived_from") or ""
    rec["note"] = kwargs.get("note") or ""
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

    core_ids = set(CORE_WEB.values())
    style_names = [str(n) for n in spec["name"]]
    cluster_names = {
        str(n) for n, flag in zip(spec["name"], spec["in_clustering"]) if int(flag) == 1
    }
    style_inputs: set[str] = set()
    for _, r in spec.iterrows():
        for expr in (str(r["numerator"]), str(r["denominator"])):
            if expr in {"D", "team_total_pass", "team_total_scoring_att", "team_touches"}:
                continue
            for tok in expr.split("+"):
                tok = tok.strip()
                if tok:
                    style_inputs.add(tok)

    # --- FPL counting / scoring stats (GW + season) ---
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
    for mid in core_ids | {"minutes", "starts"}:
        col = gw_col.get(mid, mid)
        first, last, gap = _coverage(gw, col)
        ctx = "football" if mid in football_fpl else "fantasy"
        if mid in football_fpl:
            ctx = "both"
        note_bits = [
            "vaastav merged_gw carries FPL GW stats when no live file exists; "
            "event/{gw}/live/ wins for a (season, gw) that has a file"
        ]
        if gap:
            note_bits.append(gap)
        if mid == "defensive_contribution":
            note_bits.append(
                "Fantasy always uses FPL defensive_contribution — that is what scored "
                "the points. Opta tackles/interceptions/clearances are a different "
                "canonical measurement in the football context, not a correction."
            )
        if mid in {"tackles", "recoveries", "clearances_blocks_interceptions"}:
            note_bits.append(
                "FPL and Opta both count this action and will not agree. "
                "Do not fill a gap in one feed from the other."
            )
        used_gw = _used("profile", "percentile" if mid in core_ids else "", "shrinkage" if mid in P90_METRICS else "")
        used_season = _used(
            "table" if mid in core_ids else "",
            "percentile" if mid in core_ids else "",
            "shrinkage" if mid in P90_METRICS else "",
        )
        raw_live = {
            "xg": "event.live.stats.expected_goals",
            "xa": "event.live.stats.expected_assists",
            "xgi": "event.live.stats.expected_goal_involvements",
            "xgc": "event.live.stats.expected_goals_conceded",
            "goals": "event.live.stats.goals_scored",
            "yellow_cards": "event.live.stats.yellow_cards",
            "red_cards": "event.live.stats.red_cards",
            "ict_index": "event.live.stats.ict_index",
            "total_points": "event.live.stats.total_points",
        }.get(mid, f"event.live.stats.{col}")
        rows.append(
            _row(
                id=mid,
                label=FPL_LABELS.get(mid, mid),
                feed="fpl",
                raw_field=raw_live,
                grain="gameweek",
                context=ctx,
                canonical=1,
                first_season=first,
                last_season=last,
                tier="core" if mid in core_ids else "extended",
                used_in=used_gw,
                note="; ".join(note_bits),
            )
        )
        rows.append(
            _row(
                id=mid,
                label=FPL_LABELS.get(mid, mid),
                feed="fpl",
                raw_field=f"sum(fact_player_gw.{col})",
                grain="season",
                context=ctx,
                canonical=1,
                first_season=first,
                last_season=last,
                tier="core" if mid in core_ids else "extended",
                used_in=used_season if used_season != "none" else "table",
                note="; ".join(note_bits),
            )
        )

    # Availability (season)
    av = _read(
        "fact_player_season_availability",
        ["season", "minutes", "appearances", "starts", "appearances_60_plus", "team_minutes_available", "minutes_share"],
    )
    for mid, col, derived in (
        ("appearances", "appearances", "count of matches with minutes>0"),
        ("appearances_60_plus", "appearances_60_plus", "count of matches with minutes>=60"),
        ("team_minutes_available", "team_minutes_available", "sum of club fixture minutes while at the club"),
        ("minutes_share", "minutes_share", "minutes / team_minutes_available"),
    ):
        first, last, gap = _coverage(av, col)
        rows.append(
            _row(
                id=mid,
                label=FPL_LABELS.get(mid, mid),
                feed="derived",
                raw_field=f"fact_player_season_availability.{col}",
                grain="season",
                context="both",
                canonical=1,
                first_season=first,
                last_season=last,
                tier="extended",
                used_in="table",
                derived_from=derived,
                note=gap,
            )
        )

    # Price / ownership from snapshots (exported); raw snap columns are archive.
    first_p, last_p, _ = _coverage(snap, "now_cost")
    rows.append(
        _row(
            id="price",
            label="Price",
            feed="fplcache",
            raw_field="snap_player_day.now_cost / 10",
            grain="snapshot",
            context="fantasy",
            canonical=1,
            first_season=first_p,
            last_season=last_p,
            tier="extended",
            used_in="table,profile",
            derived_from="now_cost / 10",
            note="fplcache fills historical days; own bootstrap snapshots cover the current season. Both are FPL. NULL before 2021-22 in web export.",
        )
    )
    first_o, last_o, _ = _coverage(snap, "selected_by_percent")
    rows.append(
        _row(
            id="ownership",
            label="Ownership",
            feed="fplcache",
            raw_field="snap_player_day.selected_by_percent",
            grain="snapshot",
            context="fantasy",
            canonical=1,
            first_season=first_o,
            last_season=last_o,
            tier="extended",
            used_in="table",
            note="percentage points. own_7d / own_30d are deltas vs T-7d / T-30d snapshots.",
        )
    )
    for col, label in (
        ("transfers_in_event", "Transfers in (GW)"),
        ("transfers_out_event", "Transfers out (GW)"),
        ("status", "Availability status"),
        ("chance_of_playing_next_round", "Chance of playing next round"),
        ("news", "News"),
    ):
        first, last, _ = _coverage(snap, col)
        rows.append(
            _row(
                id=col,
                label=label,
                feed="fplcache",
                raw_field=f"bootstrap.elements.{col}",
                grain="snapshot",
                context="fantasy",
                canonical=1,
                first_season=first,
                last_season=last,
                tier="archive",
                used_in="none",
                note="snap_player_day only. Never exported to web/data.",
            )
        )

    # Derived FPL rates (mart only)
    for m in P90_METRICS:
        first, last, gap = _coverage(metrics, f"{m}_p90") if not metrics.empty else (None, None, "")
        rows.append(
            _row(
                id=f"{m}_p90",
                label=f"{FPL_LABELS.get(m, m)} per 90",
                feed="derived",
                raw_field=f"fact_player_season_metrics.{m}_p90",
                grain="season",
                context="fantasy" if m not in football_fpl else "both",
                canonical=1,
                first_season=first,
                last_season=last,
                tier="extended",
                used_in="shrinkage",
                derived_from=f"{m}_total / minutes_total * 90",
                note=gap,
            )
        )
        first, last, gap = _coverage(metrics, f"{m}_adj90") if not metrics.empty else (None, None, "")
        rows.append(
            _row(
                id=f"{m}_adj90",
                label=f"{FPL_LABELS.get(m, m)} adj90",
                feed="derived",
                raw_field=f"fact_player_season_metrics.{m}_adj90",
                grain="season",
                context="fantasy" if m not in football_fpl else "both",
                canonical=1,
                first_season=first,
                last_season=last,
                tier="extended",
                used_in="shrinkage,percentile",
                derived_from=f"EB shrink of {m}_p90 toward (season, position) mean",
                note=gap,
            )
        )
    for m in SHARE_METRICS:
        first, last, gap = _coverage(metrics, f"{m}_share") if not metrics.empty else (None, None, "")
        rows.append(
            _row(
                id=f"{m}_share",
                label=f"{FPL_LABELS.get(m, m)} share of team",
                feed="derived",
                raw_field=f"fact_player_season_metrics.{m}_share",
                grain="season",
                context="fantasy" if m not in football_fpl else "both",
                canonical=1,
                first_season=first,
                last_season=last,
                tier="extended",
                used_in="percentile",
                derived_from=f"player {m} / team {m} over GWs at the club",
                note=gap,
            )
        )

    # Style features (derived Opta ratios)
    for _, spec_row in spec.iterrows():
        name = str(spec_row["name"])
        first, last, gap = _coverage(style, name)
        in_cl = int(spec_row["in_clustering"] or 0) == 1
        note_bits = []
        if not in_cl:
            note_bits.append(
                "in_clustering=0 because this measures quality, not style; "
                "clustering describes shape."
            )
        spec_first = spec_row.get("first_season")
        if pd.notna(spec_first) and str(spec_first).strip():
            note_bits.append(
                f"spec first_season={spec_first}; observed coverage below is from the mart"
            )
        if gap:
            note_bits.append(gap)
        rows.append(
            _row(
                id=name,
                label=name.replace("_", " "),
                feed="derived",
                raw_field=f"plstats.{spec_row['numerator']} / plstats.{spec_row['denominator']}",
                grain="season",
                context="football",
                canonical=1,
                first_season=first,
                last_season=last,
                tier="extended",
                used_in="clustering" if in_cl else "none",
                derived_from=f"{spec_row['numerator']} / {spec_row['denominator']}",
                note="; ".join(note_bits),
            )
        )

    # Opta season totals — archive unless a style input (the ratio is canonical, the count stays)
    skip_opta = {"season", "player_code", "pulse_id"} | set(style_names) | set(OPTA_DERIVED)
    if not opta.empty:
        for col in opta.columns:
            if col in skip_opta:
                continue
            first, last, gap = _coverage(opta, col)
            used = "clustering" if col in style_inputs else "none"
            note_bits = ["Pulse season totals. Verbatim API; omitted zeros filled only in style.py."]
            if col in style_inputs:
                note_bits.append("numerator/denominator for a style feature; the ratio is the exported metric.")
            if gap:
                note_bits.append(gap)
            rows.append(
                _row(
                    id=col,
                    label=col.replace("_", " "),
                    feed="opta",
                    raw_field=f"plstats.{col}",
                    grain="season",
                    context="football",
                    canonical=1,
                    first_season=first,
                    last_season=last,
                    tier="archive",
                    used_in=used,
                    note="; ".join(note_bits),
                )
            )

    # Explain identifiers at fixture grain (live files only)
    from transform.explain import explain_labels

    labels = explain_labels()
    live_first, live_last = _live_season_span()
    for ident, label in labels.items():
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
        rows.append(
            _row(
                id=mapped,
                label=label,
                feed="fpl",
                raw_field=f"event.live.explain[].stats.{ident}",
                grain="fixture",
                context="fantasy",
                canonical=1,
                first_season=live_first,
                last_season=live_last,
                tier="core" if mapped in core_ids else "extended",
                used_in="profile",
                note="points breakdown per fixture. Omitted from matchlogs when the GW has no live file. Include every component FPL returned, including zero-point minutes.",
            )
        )

    out = pd.DataFrame(rows, columns=REGISTER_COLUMNS)
    out["canonical"] = pd.to_numeric(out["canonical"], errors="coerce").fillna(0).astype(int)
    out = out.drop_duplicates(subset=["id", "grain", "context", "feed"], keep="first")
    out = out.sort_values(["tier", "grain", "id", "context"]).reset_index(drop=True)
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
        for k in ("first_season", "last_season", "derived_from", "note", "used_in"):
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
    work["family"] = work["feed"].map(lambda f: "opta" if f == "opta" else ("fpl" if f in FPL_FEEDS else f))
    for (grain, context, concept), g in work.groupby(["grain", "context", "concept"], dropna=False):
        families = set(g["family"])
        if "fpl" in families and "opta" in families:
            ids = ", ".join(sorted(set(g["id"].astype(str))))
            flags.append(
                f"DUAL CANONICAL {concept} at grain={grain} context={context}: {ids}"
            )
    # Same id, grain, context, two canonical feeds
    for (mid, grain, context), g in work.groupby(["id", "grain", "context"], dropna=False):
        feeds = sorted(set(g["feed"].astype(str)))
        if len(feeds) > 1:
            flags.append(
                f"DUAL CANONICAL id={mid} grain={grain} context={context} feeds={feeds}"
            )
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
                    else:
                        walk(v, k)
                    continue
                keys.add(str(k))
                walk(v, k)
        elif isinstance(obj, list):
            for item in obj:
                walk(item, parent)

    for path in sorted(web_dir.glob("*.json")):
        if path.name == "metrics_register.json":
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
        return key
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
    return key


def validate_metric_register(df: pd.DataFrame, web_dir: Path | None = None) -> list[str]:
    errors: list[str] = []
    flags = flag_dual_canonical(df)
    for f in flags:
        errors.append(f)

    ids = set(zip(df["id"].astype(str), df["grain"].astype(str)))
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

    # Archive values must not appear as data keys (style feature names are extended).
    archive_ids = set(df.loc[df["tier"] == "archive", "id"].astype(str))
    shipped_ids = set(df.loc[df["tier"] != "archive", "id"].astype(str))
    archive_only = archive_ids - shipped_ids
    leaked = [k for k in web_keys if json_key_to_id(k) in archive_only]
    # clustering inputs listed only as style ratios, not raw opta names — raw names leaking is an error
    if leaked:
        errors.append("archive ids exported as web/data keys: " + ", ".join(sorted(leaked)[:40]))

    # first_season must be a real season string when present
    bad_first = df.loc[df["first_season"].astype(str).str.len() > 0]
    for rec in bad_first.to_dict("records"):
        fs = str(rec["first_season"])
        if len(fs) != 7 or fs[4] != "-":
            errors.append(f"bad first_season for {rec['id']}: {fs!r}")

    _ = ids
    return errors


def print_tier_counts(df: pd.DataFrame) -> None:
    print("\n== metric register tiers ==", flush=True)
    print(df.groupby("tier").size().rename("n_rows").to_string(), flush=True)
    print("\nunique ids per tier:", flush=True)
    print(df.groupby("tier")["id"].nunique().rename("n_ids").to_string(), flush=True)
    print("\nby tier, grain:", flush=True)
    print(df.groupby(["tier", "grain"]).size().rename("n").to_string(), flush=True)


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


if __name__ == "__main__":
    main()
