"""Manual diagnostic: fact_player_gw GW1-3 vs live element-summary history.

Not part of the update cron.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import duckdb
import pandas as pd

from ingest.client import FplClient, read_json, season_from_bootstrap
from ingest.paths import DB_PATH, RAW

PLAYERS = ("Ødegaard", "Odegaard", "Saka", "Havertz", "Calafiori")
GW_FROM, GW_TO = 1, 3
METRICS = (
    ("expected_goals", "expected_goals", 0.02),
    ("expected_assists", "expected_assists", 0.02),
    ("minutes", "minutes", 0),
    ("total_points", "total_points", 0),
)


def _match_elements(bootstrap: dict) -> list[dict]:
    wanted = {n.casefold() for n in PLAYERS}
    found: dict[str, dict] = {}
    for el in bootstrap.get("elements") or []:
        web = str(el.get("web_name") or "")
        second = str(el.get("second_name") or "")
        if web.casefold() in wanted or any(w in web.casefold() for w in ("ødegaard", "odegaard")):
            key = web
            if "odegaard" in web.casefold() or "ødegaard" in web.casefold():
                key = "Ødegaard"
            found[key] = el
        elif second.casefold() in wanted:
            found[web] = el
        elif any(n.casefold() in web.casefold() or n.casefold() in second.casefold() for n in ("saka", "havertz", "calafiori")):
            found[web] = el
    # Prefer the four named attackers/defenders, drop extras.
    prefer = []
    for label in ("Ødegaard", "Saka", "Havertz", "Calafiori"):
        hit = found.get(label)
        if hit is None:
            for web, el in found.items():
                if label.casefold() in web.casefold() or label.casefold() in str(el.get("second_name") or "").casefold():
                    hit = el
                    break
        if hit is not None:
            prefer.append(hit)
    return prefer


def _history_gw_sum(payload: dict) -> dict:
    rows = payload.get("history") or []
    frame = pd.DataFrame(rows)
    if frame.empty or "round" not in frame.columns:
        return {m: 0 for m, _, _ in METRICS}
    sub = frame[(frame["round"] >= GW_FROM) & (frame["round"] <= GW_TO)]
    out = {}
    for col, _, _ in METRICS:
        out[col] = pd.to_numeric(sub[col], errors="coerce").sum() if col in sub.columns else 0
    return out


def main() -> None:
    if not DB_PATH.exists():
        raise SystemExit(f"missing {DB_PATH}; run ingest/update.py first")
    con = duckdb.connect(str(DB_PATH), read_only=True)
    with FplClient() as client:
        bootstrap = client.get_json("https://fantasy.premierleague.com/api/bootstrap-static/")
        season = season_from_bootstrap(bootstrap)
        elements = _match_elements(bootstrap)
        if len(elements) < 4:
            names = [e.get("web_name") for e in elements]
            raise SystemExit(f"expected 4 players, found {names}")

        rows = []
        mismatches = 0
        for el in elements:
            code = el["code"]
            cache = RAW / "element-summary" / f"{code}.json"
            if cache.exists():
                payload = read_json(cache)
            else:
                payload = client.get_json(
                    f"https://fantasy.premierleague.com/api/element-summary/{el['id']}/"
                )
            api = _history_gw_sum(payload)
            fact = con.execute(
                """
                SELECT
                  COALESCE(SUM(expected_goals), 0) AS expected_goals,
                  COALESCE(SUM(expected_assists), 0) AS expected_assists,
                  COALESCE(SUM(minutes), 0) AS minutes,
                  COALESCE(SUM(total_points), 0) AS total_points
                FROM fact_player_gw
                WHERE season = ? AND player_code = ? AND gw BETWEEN ? AND ?
                """,
                [season, code, GW_FROM, GW_TO],
            ).fetchone()
            fact_map = {
                "expected_goals": float(fact[0]),
                "expected_assists": float(fact[1]),
                "minutes": float(fact[2]),
                "total_points": float(fact[3]),
            }
            boot = {
                "expected_goals": float(el.get("expected_goals") or 0),
                "expected_assists": float(el.get("expected_assists") or 0),
                "minutes": float(el.get("minutes") or 0),
                "total_points": float(el.get("total_points") or 0),
            }
            for col, _, tol in METRICS:
                delta = abs(fact_map[col] - float(api[col]))
                ok = delta <= tol
                if not ok:
                    mismatches += 1
                rows.append(
                    {
                        "player": el.get("web_name"),
                        "metric": col,
                        "fact_gw1_3": round(fact_map[col], 4),
                        "element_summary_gw1_3": round(float(api[col]), 4),
                        "bootstrap_season": round(boot[col], 4),
                        "delta_vs_summary": round(delta, 4),
                        "ok": ok,
                    }
                )

    table = pd.DataFrame(rows)
    print(table.to_string(index=False))
    print()
    print(
        "bootstrap_season is the live season-to-date total (includes GWs after "
        f"{GW_TO} if already played). Comparison that must match is fact vs element-summary history GW{GW_FROM}-{GW_TO}."
    )
    if mismatches:
        raise SystemExit(f"spot check FAILED: {mismatches} mismatches")
    print("spot check passed")


if __name__ == "__main__":
    main()
