from __future__ import annotations

import sys

import pandas as pd


class MartValidationError(AssertionError):
    pass


def _print_table(title: str, df: pd.DataFrame) -> None:
    print(f"\n== {title} ==")
    text = df.to_string(index=False)
    encoding = getattr(sys.stdout, "encoding", None) or "utf-8"
    sys.stdout.buffer.write((text + "\n").encode(encoding, errors="replace"))
    sys.stdout.buffer.flush()


def validate_marts(con, extra_checks: dict) -> None:
    errors: list[str] = []

    season_counts = con.execute(
        """
        SELECT season, COUNT(*) AS rows, COUNT(DISTINCT player_code) AS players
        FROM fact_player_gw
        GROUP BY season
        ORDER BY season
        """
    ).df()
    _print_table("fact_player_gw row count per season", season_counts)
    if season_counts.empty:
        errors.append("fact_player_gw is empty")

    played = con.execute(
        """
        SELECT season, gw,
               COUNT(DISTINCT player_code) FILTER (WHERE minutes > 0) AS players_with_minutes
        FROM fact_player_gw
        GROUP BY season, gw
        ORDER BY season, gw
        """
    ).df()
    # Print a compact summary rather than every GW.
    summary = (
        played.groupby("season", as_index=False)
        .agg(
            gws=("gw", "nunique"),
            min_played=("players_with_minutes", "min"),
            median_played=("players_with_minutes", "median"),
            max_played=("players_with_minutes", "max"),
        )
    )
    _print_table("distinct players with minutes > 0 per GW (season summary)", summary)

    bad_zero = played[played["players_with_minutes"] == 0]
    if not bad_zero.empty:
        errors.append(
            "finished GW with 0 players minutes>0: "
            + bad_zero.head(10).to_string(index=False)
        )
    too_many = played[played["players_with_minutes"] > 900]
    if not too_many.empty:
        errors.append("GW with >900 players minutes>0 -- likely a join explosion")

    xg = con.execute(
        """
        SELECT season,
               COUNT(*) FILTER (WHERE minutes > 0 AND expected_goals IS NULL) AS null_xg_played,
               COUNT(*) FILTER (WHERE minutes > 0) AS played
        FROM fact_player_gw
        WHERE season >= '2022-23'
        GROUP BY season
        ORDER BY season
        """
    ).df()
    _print_table("NULL expected_goals among minutes>0 (seasons >= 2022-23)", xg)
    for _, row in xg.iterrows():
        if row["season"] >= "2023-24" and row["played"] > 0:
            share = row["null_xg_played"] / row["played"]
            if share > 0.02:
                errors.append(
                    f"{row['season']}: {row['null_xg_played']}/{row['played']} "
                    f"played rows have NULL expected_goals (share={share:.1%})"
                )

    missing = con.execute(
        """
        SELECT COUNT(*) AS n
        FROM fact_player_gw f
        WHERE f.player_code IS NULL
           OR NOT EXISTS (
                SELECT 1 FROM dim_player d WHERE d.code = f.player_code
           )
        """
    ).fetchone()[0]
    print(f"\n== player_code in fact_player_gw missing from dim_player: {missing} ==")
    if missing:
        errors.append(f"{missing} fact_player_gw.player_code values missing from dim_player")

    dupes = con.execute(
        """
        SELECT season, gw, player_code, COUNT(*) AS n
        FROM fact_player_gw
        GROUP BY season, gw, player_code
        HAVING COUNT(*) > 1
        """
    ).df()
    print(f"== duplicate (season, gw, player_code) rows: {len(dupes)} ==")
    if not dupes.empty:
        print(dupes.to_string(index=False))
        errors.append(f"duplicate fact_player_gw keys: {len(dupes)}")

    transfers = con.execute(
        """
        SELECT season, COUNT(*) AS players_with_multiple_teams
        FROM (
            SELECT season, player_code, COUNT(DISTINCT team_code) AS n_teams
            FROM fact_player_gw
            WHERE team_code IS NOT NULL
            GROUP BY season, player_code
            HAVING COUNT(DISTINCT team_code) > 1
        )
        GROUP BY season
        ORDER BY season
        """
    ).df()
    _print_table("players with >1 team_code in a season (transfer sanity)", transfers)

    sources = con.execute(
        """
        SELECT season, gw, value_source, COUNT(*) AS n,
               COUNT(value) AS with_value, COUNT(xp) AS with_xp
        FROM fact_player_gw
        WHERE season = (SELECT max(season) FROM fact_player_gw)
        GROUP BY season, gw, value_source
        ORDER BY gw, value_source
        """
    ).df()
    _print_table("current season value_source / xp coverage", sources)

    leaked_snap = con.execute(
        """
        SELECT COUNT(*) FROM fact_player_gw
        WHERE season = '2026-27' AND gw IN (1, 2, 3) AND value_source = 'snapshot'
        """
    ).fetchone()[0]
    print(f"== 2026-27 GW1-3 rows with value_source=snapshot (should be 0): {leaked_snap} ==")
    if leaked_snap:
        errors.append(
            "pre-snapshot-era GWs must not use value_source=snapshot "
            f"({leaked_snap} rows in 2026-27 GW1-3)"
        )

    _validate_club_fixtures(con, errors)
    _validate_derived(con, errors)
    _validate_snap_player_day(con, errors)
    _validate_opta(con, errors)
    _validate_style(con, errors)
    _validate_clusters(con, errors)

    if errors:
        raise MartValidationError("mart validation failed:\n- " + "\n- ".join(errors))
    print("\nvalidation passed")


def _validate_club_fixtures(con, errors: list[str]) -> None:
    """Every club plays 38 fixtures. Blanks (no fixture that GW) should match extras from doubles."""
    tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    if "fact_fixture" not in tables:
        errors.append("missing fact_fixture")
        return

    club = con.execute(
        """
        WITH stacked AS (
            SELECT season, gw, home_team_code AS team_code FROM fact_fixture
            UNION ALL
            SELECT season, gw, away_team_code FROM fact_fixture
        ),
        club_gw AS (
            SELECT season, gw, team_code, COUNT(*) AS n
            FROM stacked
            WHERE team_code IS NOT NULL AND gw IS NOT NULL
            GROUP BY 1, 2, 3
        ),
        club_season AS (
            SELECT season, team_code,
                   SUM(n) AS fixtures,
                   COUNT(*) FILTER (WHERE n > 1) AS double_gws,
                   COALESCE(SUM(n - 1) FILTER (WHERE n > 1), 0) AS extras
            FROM club_gw
            GROUP BY 1, 2
        ),
        season_gws AS (
            SELECT DISTINCT season, gw FROM fact_fixture WHERE gw IS NOT NULL
        ),
        blanks AS (
            SELECT c.season, c.team_code,
                   COUNT(*) FILTER (WHERE cg.team_code IS NULL) AS blanks
            FROM (SELECT DISTINCT season, team_code FROM club_season) c
            JOIN season_gws s ON s.season = c.season
            LEFT JOIN club_gw cg
              ON cg.season = c.season
             AND cg.team_code = c.team_code
             AND cg.gw = s.gw
            GROUP BY 1, 2
        )
        SELECT cs.season, cs.team_code, cs.fixtures, cs.double_gws, cs.extras, b.blanks
        FROM club_season cs
        JOIN blanks b USING (season, team_code)
        ORDER BY cs.season, cs.team_code
        """
    ).df()
    if club.empty:
        errors.append("fact_fixture has no club-gw rows")
        return

    summary = (
        club.groupby("season", as_index=False)
        .agg(
            clubs=("team_code", "nunique"),
            min_fixtures=("fixtures", "min"),
            max_fixtures=("fixtures", "max"),
            blanks=("blanks", "sum"),
            double_gws=("double_gws", "sum"),
            extras=("extras", "sum"),
        )
    )
    imbal_n = (
        club.assign(_im=club["blanks"] != club["extras"])
        .groupby("season", as_index=False)["_im"]
        .sum()
        .rename(columns={"_im": "imbalance_clubs"})
    )
    summary = summary.merge(imbal_n, on="season")
    _print_table(
        "club fixtures per season (38 each; blanks should match extra DGW fixtures)",
        summary,
    )

    bad = club[club["fixtures"] != 38]
    if not bad.empty:
        sample = bad.head(25)
        _print_table("clubs whose season fixture count is not 38", sample)
        errors.append(
            f"{len(bad)} club-seasons do not have 38 fixtures "
            f"(min={int(bad['fixtures'].min())}, max={int(bad['fixtures'].max())})"
        )

    imbal = club[club["blanks"] != club["extras"]]
    if imbal.empty:
        print("== club blank GWs vs extra DGW fixtures: balanced ==")
    else:
        n = len(imbal)
        print(
            f"== club blank GWs vs extra DGW fixtures: {n} club-seasons differ "
            "(informational when a GW number is missing for everyone, e.g. 2022-23 GW7) =="
        )
        _print_table(
            "sample clubs with blanks != extras",
            imbal.head(15),
        )

    if "fact_player_fixture" in tables:
        dupes = con.execute(
            """
            SELECT season, gw, player_code, fixture_id, COUNT(*) AS n
            FROM fact_player_fixture
            GROUP BY 1, 2, 3, 4
            HAVING COUNT(*) > 1
            """
        ).df()
        print(f"== duplicate fact_player_fixture keys: {len(dupes)} ==")
        if not dupes.empty:
            print(dupes.head(15).to_string(index=False))
            errors.append(f"duplicate fact_player_fixture keys: {len(dupes)}")


def _validate_derived(con, errors: list[str]) -> None:
    tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    needed = {
        "fact_team_gw",
        "fact_player_season_metrics",
        "fact_player_season_availability",
        "dim_metric",
        "dim_region",
        "dim_shrinkage",
    }
    missing = needed - tables
    if missing:
        errors.append("missing derived marts: " + ", ".join(sorted(missing)))
        return

    impossible = con.execute(
        """
        SELECT
            COUNT(*) FILTER (
                WHERE starts IS NOT NULL AND appearances IS NOT NULL
                  AND starts > appearances
            ) AS starts_gt_apps,
            COUNT(*) FILTER (
                WHERE appearances > 0 AND minutes IS NOT NULL
                  AND minutes * 1.0 / appearances > 90
            ) AS minutes_per_app_gt_90,
            COUNT(*) FILTER (
                WHERE appearances_60_plus IS NOT NULL AND appearances IS NOT NULL
                  AND appearances_60_plus > appearances
            ) AS apps60_gt_apps
        FROM fact_player_season_availability
        """
    ).df().iloc[0]
    n_starts = int(impossible["starts_gt_apps"])
    n_mpa = int(impossible["minutes_per_app_gt_90"])
    n_60 = int(impossible["apps60_gt_apps"])
    print(
        "== appearance impossibles: "
        f"starts>appearances {n_starts}, "
        f"minutes/appearances>90 {n_mpa}, "
        f"apps_60>appearances {n_60} =="
    )
    if n_starts or n_mpa or n_60:
        sample = con.execute(
            """
            SELECT a.season, a.grain, d.web_name, a.minutes, a.starts,
                   a.appearances, a.appearances_60_plus, a.minutes_per_appearance
            FROM fact_player_season_availability a
            LEFT JOIN dim_player d ON d.code = a.player_code
            WHERE (a.starts IS NOT NULL AND a.appearances IS NOT NULL
                   AND a.starts > a.appearances)
               OR (a.appearances > 0 AND a.minutes IS NOT NULL
                   AND a.minutes * 1.0 / a.appearances > 90)
               OR (a.appearances_60_plus IS NOT NULL AND a.appearances IS NOT NULL
                   AND a.appearances_60_plus > a.appearances)
            ORDER BY a.season, a.grain, d.web_name
            LIMIT 25
            """
        ).df()
        _print_table("structurally impossible appearance rows", sample)
        if n_starts:
            errors.append(
                f"{n_starts} availability rows with starts > appearances"
            )
        if n_mpa:
            errors.append(
                f"{n_mpa} availability rows with minutes / appearances > 90"
            )
        if n_60:
            errors.append(
                f"{n_60} availability rows with appearances_60_plus > appearances"
            )

    share_cols = [
        r[0]
        for r in con.execute(
            """
            SELECT column_name
            FROM information_schema.columns
            WHERE table_name = 'fact_player_season_metrics'
              AND column_name LIKE '%_share'
            """
        ).fetchall()
    ]
    if share_cols:
        over = " OR ".join(
            f'("{c}" IS NOT NULL AND ("{c}" < 0 OR "{c}" > 1.0000001))' for c in share_cols
        )
        n_over = con.execute(
            f"SELECT COUNT(*) FROM fact_player_season_metrics WHERE {over}"
        ).fetchone()[0]
        print(f"== share columns outside [0, 1]: {n_over} ==")
        if n_over:
            errors.append(f"{n_over} share values outside [0, 1]")

        high_pred = " OR ".join(f'("{c}" > 0.5)' for c in share_cols)
        high = con.execute(
            f"""
            SELECT m.season, m.grain, m.player_code, d.web_name, m.team_code, m.element_type,
                   m.minutes_total,
                   {", ".join(f'm."{c}"' for c in share_cols)}
            FROM fact_player_season_metrics m
            LEFT JOIN dim_player d ON d.code = m.player_code
            WHERE m.grain IN ('season', 'spell') AND m.season = '2025-26' AND ({high_pred})
            ORDER BY m.xg_share DESC NULLS LAST
            """
        ).df()
        if high.empty:
            mx = con.execute(
                """
                SELECT d.web_name, m.xg_share, m.goals_share
                FROM fact_player_season_metrics m
                JOIN dim_player d ON d.code = m.player_code
                WHERE m.grain = 'season' AND m.season = '2025-26'
                ORDER BY m.xg_share DESC NULLS LAST
                LIMIT 1
                """
            ).df()
            extra = ""
            if not mx.empty:
                extra = (
                    f" (max xg_share={mx.iloc[0]['xg_share']:.3f} {mx.iloc[0]['web_name']})"
                )
            print(f"\n== 2025-26 shares > 0.5: none{extra} ==")
        else:
            _print_table("2025-26 shares > 0.5 (rare but real)", high)

    top = con.execute(
        """
        SELECT d.web_name, m.minutes_total, m.goals_conceded_total, m.goals_conceded_p90,
               m.goals_conceded_adj90
        FROM fact_player_season_metrics m
        JOIN dim_player d ON d.code = m.player_code
        WHERE m.grain = 'season' AND m.season = '2025-26' AND m.element_type = 1
          AND m.goals_conceded_adj90 IS NOT NULL
        ORDER BY m.goals_conceded_adj90 ASC, m.minutes_total DESC
        LIMIT 5
        """
    ).df()
    _print_table(
        "2025-26 keepers TOP 5 by fewest goals_conceded_adj90",
        top,
    )
    bot = con.execute(
        """
        SELECT d.web_name, m.minutes_total, m.goals_conceded_total, m.goals_conceded_p90,
               m.goals_conceded_adj90
        FROM fact_player_season_metrics m
        JOIN dim_player d ON d.code = m.player_code
        WHERE m.grain = 'season' AND m.season = '2025-26' AND m.element_type = 1
          AND m.goals_conceded_adj90 IS NOT NULL
        ORDER BY m.goals_conceded_adj90 DESC, m.minutes_total DESC
        LIMIT 5
        """
    ).df()
    _print_table(
        "2025-26 keepers BOTTOM 5 by most goals_conceded_adj90",
        bot,
    )

    kinsky = con.execute(
        """
        SELECT d.web_name, m.minutes_total,
               m.goals_conceded_total, m.goals_conceded_p90, m.goals_conceded_adj90,
               m.xgc_p90, m.xgc_adj90,
               m.saves_p90, m.saves_adj90
        FROM fact_player_season_metrics m
        JOIN dim_player d ON d.code = m.player_code
        WHERE m.grain = 'season' AND m.season = '2025-26'
          AND d.web_name ILIKE '%kinsky%'
        """
    ).df()
    _print_table("Kinsky 2025-26 adj90 vs raw p90", kinsky)

    hi = con.execute(
        """
        SELECT d.web_name, m.element_type, m.minutes_total,
               m.xg_p90, m.xg_adj90, m.xg_adj90 - m.xg_p90 AS xg_delta,
               m.goals_p90, m.goals_adj90
        FROM fact_player_season_metrics m
        JOIN dim_player d ON d.code = m.player_code
        WHERE m.grain = 'season' AND m.season = '2025-26'
        ORDER BY m.minutes_total DESC NULLS LAST
        LIMIT 10
        """
    ).df()
    _print_table("2025-26 10 highest-minute players (adj90 should ~ p90)", hi)

    lo = con.execute(
        """
        SELECT d.web_name, m.element_type, m.minutes_total,
               m.xg_p90, m.xg_adj90,
               m.goals_p90, m.goals_adj90
        FROM fact_player_season_metrics m
        JOIN dim_player d ON d.code = m.player_code
        WHERE m.grain = 'season' AND m.season = '2025-26'
          AND m.minutes_total >= 180
        ORDER BY m.minutes_total ASC, d.web_name
        LIMIT 10
        """
    ).df()
    _print_table("2025-26 10 lowest-minute players in 180-min pool (adj90 should sit near pool mean)", lo)

    shrink = con.execute(
        """
        SELECT element_type, metric, k, method, COUNT(*) AS seasons
        FROM dim_shrinkage
        GROUP BY element_type, metric, k, method
        ORDER BY element_type, metric
        """
    ).df()
    _print_table("dim_shrinkage fitted k (nineties of prior strength)", shrink)

    mismatch = con.execute(
        """
        WITH spells AS (
            SELECT season, player_code,
                   SUM(minutes_total) AS minutes_total,
                   SUM(xg_total) AS xg_total,
                   SUM(goals_total) AS goals_total
            FROM fact_player_season_metrics
            WHERE grain = 'spell' AND spell_count > 1
            GROUP BY season, player_code
        ),
        seasons AS (
            SELECT season, player_code, minutes_total, xg_total, goals_total
            FROM fact_player_season_metrics
            WHERE grain = 'season' AND spell_count > 1
        )
        SELECT COUNT(*) FROM spells s
        JOIN seasons t USING (season, player_code)
        WHERE abs(coalesce(s.minutes_total, 0) - coalesce(t.minutes_total, 0)) > 1e-6
           OR abs(coalesce(s.xg_total, 0) - coalesce(t.xg_total, 0)) > 1e-6
           OR abs(coalesce(s.goals_total, 0) - coalesce(t.goals_total, 0)) > 1e-6
        """
    ).fetchone()[0]
    print(f"== transferred players whose spell totals != season totals: {mismatch} ==")
    if mismatch:
        errors.append(
            f"{mismatch} transferred players have spell rows that do not sum to season totals"
        )

    pools = con.execute(
        """
        SELECT season, element_type,
               COUNT(*) FILTER (WHERE minutes_total >= 180) AS qualified_180,
               COUNT(*) FILTER (WHERE minutes_total >= 450) AS qualified_450
        FROM fact_player_season_metrics
        WHERE grain = 'season'
        GROUP BY season, element_type
        ORDER BY season, element_type
        """
    ).df()
    _print_table("players with minutes >= 180 vs 450 (season, element_type)", pools)
    extra = pools[pools["season"] == "2025-26"].copy()
    if not extra.empty:
        extra["added"] = extra["qualified_180"] - extra["qualified_450"]
        _print_table("2025-26 additional players at 180 vs 450 minutes", extra)
    small = pools[pools["qualified_180"] < 20]
    if not small.empty:
        print("\n== FLAG: position-season pools under 20 at 180 minutes ==")
        print(small.to_string(index=False))

    if "current_players" in tables:
        coverage = con.execute(
            """
            SELECT
                COUNT(*) AS current_players,
                COUNT(*) FILTER (WHERE p.region IS NULL) AS null_region,
                COUNT(*) FILTER (
                    WHERE p.region IS NOT NULL AND r.country_name IS NULL
                ) AS region_id_unmapped,
                COUNT(*) FILTER (
                    WHERE p.region IS NULL OR r.country_name IS NULL
                ) AS unmapped
            FROM current_players c
            LEFT JOIN dim_player p ON p.code = c.player_code
            LEFT JOIN dim_region r ON r.region_id = p.region
            """
        ).df()
        _print_table("current-squad region mapping coverage", coverage)
        unmapped = con.execute(
            """
            SELECT d.web_name, d.first_name, d.second_name, d.region
            FROM current_players c
            JOIN dim_player d ON d.code = c.player_code
            LEFT JOIN dim_region r ON r.region_id = d.region
            WHERE d.region IS NULL OR r.country_name IS NULL
            ORDER BY d.second_name, d.first_name
            """
        ).df()
        if not unmapped.empty:
            _print_table("unmapped current players", unmapped)
        spot = con.execute(
            """
            SELECT d.web_name, d.region, r.country_name, r.iso_alpha2, r.iso_alpha3
            FROM current_players c
            JOIN dim_player d ON d.code = c.player_code
            LEFT JOIN dim_region r ON r.region_id = d.region
            WHERE d.web_name IN ('Saka', 'Haaland', 'M.Salah', 'Raya', 'A.Becker', 'Palmer')
            ORDER BY d.web_name, d.first_name
            """
        ).df()
        _print_table("known-player region spot check", spot)

    n_team = con.execute("SELECT COUNT(*) FROM fact_team_gw").fetchone()[0]
    if n_team == 0:
        errors.append("fact_team_gw is empty")
        return
    cols = {r[0] for r in con.execute("DESCRIBE fact_team_gw").fetchall()}
    for need in ("goals_conceded", "clean_sheets", "xgc", "xg"):
        if need not in cols:
            errors.append(f"fact_team_gw missing {need}")
            return
    chk = con.execute(
        """
        SELECT
            max(xgc) AS xgc_max,
            median(xgc) AS xgc_med,
            max(goals_conceded) AS gc_max,
            sum(xg) AS xg_for,
            sum(xgc) AS xg_against
        FROM fact_team_gw
        WHERE season = '2024-25' AND gw = 1 AND matches_played = 1
        """
    ).fetchone()
    if chk is not None:
        xgc_max, xgc_med, gc_max, xg_for, xg_against = chk
        print(
            f"== fact_team_gw 2024-25 GW1  xgc max={xgc_max} med={xgc_med}  "
            f"gc max={gc_max}  xg={xg_for} xgc_sum={xg_against} ==",
            flush=True,
        )
        if xgc_max is not None and float(xgc_max) > 8:
            errors.append(
                f"fact_team_gw.xgc looks player-summed "
                f"(2024-25 GW1 max {xgc_max})"
            )
        if gc_max is not None and int(gc_max) > 10:
            errors.append(
                f"fact_team_gw.goals_conceded looks player-summed "
                f"(2024-25 GW1 max {gc_max})"
            )
        if (
            xg_for is not None
            and xg_against is not None
            and abs(float(xg_for) - float(xg_against)) > 0.05
        ):
            errors.append(
                f"fact_team_gw 2024-25 GW1 xg {xg_for} != xgc {xg_against}"
            )


def _validate_snap_player_day(con, errors: list[str]) -> None:
    tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    if "snap_player_day" not in tables:
        errors.append("missing snap_player_day")
        return

    n = con.execute("SELECT COUNT(*) FROM snap_player_day").fetchone()[0]
    print(f"== snap_player_day rows: {n} ==")
    if n == 0:
        errors.append("snap_player_day is empty")
        return

    dupes = con.execute(
        """
        SELECT snapshot_ts, player_code, source, COUNT(*) AS n
        FROM snap_player_day
        GROUP BY 1, 2, 3
        HAVING COUNT(*) > 1
        """
    ).df()
    print(f"== duplicate (snapshot_ts, player_code, source): {len(dupes)} ==")
    if not dupes.empty:
        print(dupes.head(15).to_string(index=False))
        errors.append(
            f"duplicate snap_player_day keys: {len(dupes)}"
        )

    same_ts = con.execute(
        """
        SELECT snapshot_ts, player_code, COUNT(*) AS n,
               COUNT(DISTINCT source) AS sources
        FROM snap_player_day
        GROUP BY 1, 2
        HAVING COUNT(*) > 1
        """
    ).df()
    if not same_ts.empty:
        print(
            f"== same snapshot_ts+player_code from multiple sources: {len(same_ts)} "
            "(kept; timestamp is the data) =="
        )

    decreasing = con.execute(
        """
        WITH ordered AS (
            SELECT
                player_code,
                snapshot_ts,
                LAG(snapshot_ts) OVER (
                    PARTITION BY player_code
                    ORDER BY snapshot_ts, source
                ) AS prev_ts
            FROM snap_player_day
        )
        SELECT COUNT(*) FROM ordered
        WHERE prev_ts IS NOT NULL AND snapshot_ts < prev_ts
        """
    ).fetchone()[0]
    print(f"== snapshot_ts decreases per player_code: {decreasing} ==")
    if decreasing:
        errors.append(
            f"snapshot_ts not strictly increasing per player_code ({decreasing} rows)"
        )

    counts = con.execute(
        """
        SELECT snapshot_ts, source, COUNT(*) AS players
        FROM snap_player_day
        GROUP BY 1, 2
        """
    ).df()
    bad = counts[(counts["players"] < 300) | (counts["players"] > 900)]
    print(
        f"== snapshots with player count outside 300-900: {len(bad)} "
        f"of {len(counts)} =="
    )
    if not bad.empty:
        print(bad.head(20).to_string(index=False))
        errors.append(
            f"{len(bad)} snapshots have player count outside 300-900"
        )

    days = con.execute(
        """
        SELECT season, COUNT(DISTINCT substring(CAST(snapshot_ts AS VARCHAR), 1, 10)) AS days
        FROM snap_player_day
        GROUP BY season
        ORDER BY season
        """
    ).df()
    _print_table("distinct snapshot days by season", days)

    jumps = con.execute(
        """
        WITH ordered AS (
            SELECT
                player_code,
                snapshot_ts,
                source,
                now_cost,
                LAG(now_cost) OVER (
                    PARTITION BY player_code
                    ORDER BY snapshot_ts, source
                ) AS prev_cost
            FROM snap_player_day
            WHERE now_cost IS NOT NULL
        )
        SELECT
            player_code,
            snapshot_ts,
            source,
            prev_cost,
            now_cost,
            now_cost - prev_cost AS tenths
        FROM ordered
        WHERE prev_cost IS NOT NULL AND abs(now_cost - prev_cost) > 3
        ORDER BY abs(now_cost - prev_cost) DESC, snapshot_ts
        """
    ).df()
    print(
        f"== consecutive now_cost changes > 0.3 ({len(jumps)} rows; "
        "season boundaries or bad joins; not a failure) =="
    )
    if not jumps.empty:
        print(jumps.head(80).to_string(index=False))
        if len(jumps) > 80:
            print(f"  ... {len(jumps) - 80} more")

    by_src = con.execute(
        """
        SELECT source, COUNT(*) AS rows,
               COUNT(DISTINCT substring(CAST(snapshot_ts AS VARCHAR), 1, 10)) AS days,
               COUNT(DISTINCT player_code) AS players
        FROM snap_player_day
        GROUP BY source
        ORDER BY source
        """
    ).df()
    _print_table("snap_player_day by source", by_src)

    if "dim_player" in tables:
        named = con.execute(
            """
            SELECT d.code, d.web_name, d.first_name, d.second_name
            FROM dim_player d
            WHERE d.web_name IN ('M.Salah', 'Salah')
               OR d.second_name ILIKE 'Salah'
            ORDER BY d.code
            LIMIT 5
            """
        ).df()
        if not named.empty:
            code = int(named.iloc[0]["code"])
            label = named.iloc[0]["web_name"]
            stats = con.execute(
                """
                WITH ordered AS (
                    SELECT
                        now_cost,
                        snapshot_ts,
                        LAG(now_cost) OVER (ORDER BY snapshot_ts, source) AS prev
                    FROM snap_player_day
                    WHERE player_code = ?
                )
                SELECT
                    COUNT(*) AS rows,
                    COUNT(DISTINCT substring(CAST(snapshot_ts AS VARCHAR), 1, 10)) AS days,
                    MIN(substring(CAST(snapshot_ts AS VARCHAR), 1, 10)) AS earliest,
                    MAX(substring(CAST(snapshot_ts AS VARCHAR), 1, 10)) AS latest,
                    COUNT(*) FILTER (
                        WHERE prev IS NOT NULL AND now_cost IS DISTINCT FROM prev
                    ) AS price_changes
                FROM ordered
                """,
                [code],
            ).df()
            print(
                f"== {label} (player_code={code}) price series: "
                f"{stats.to_string(index=False)} =="
            )


def _validate_opta(con, errors: list[str]) -> None:
    tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    if "dim_player" in tables:
        opta_null = con.execute(
            "SELECT COUNT(*) FROM dim_player WHERE opta_code IS NULL"
        ).fetchone()[0]
        n = con.execute("SELECT COUNT(*) FROM dim_player").fetchone()[0]
        print(f"== dim_player opta_code NULL: {opta_null}/{n} ==")
        if n and opta_null == n:
            errors.append("dim_player.opta_code is still all NULL")

    if "fact_player_season_opta" not in tables:
        return
    counts = con.execute(
        """
        SELECT season, COUNT(*) AS players
        FROM fact_player_season_opta
        GROUP BY season
        ORDER BY season
        """
    ).df()
    _print_table("fact_player_season_opta players per season", counts)
    dupes = con.execute(
        """
        SELECT season, player_code, COUNT(*) AS n
        FROM fact_player_season_opta
        GROUP BY 1, 2
        HAVING COUNT(*) > 1
        """
    ).df()
    if not dupes.empty:
        errors.append(f"duplicate fact_player_season_opta keys: {len(dupes)}")

    cols = {r[0] for r in con.execute("DESCRIBE fact_player_season_opta").fetchall()}
    if "pass_accuracy" in cols and "total_pass" in cols:
        bad = con.execute(
            """
            SELECT COUNT(*) FROM fact_player_season_opta
            WHERE pass_accuracy IS NOT NULL
              AND (total_pass IS NULL OR total_pass = 0)
            """
        ).fetchone()[0]
        if bad:
            errors.append(
                f"{bad} pass_accuracy values where total_pass is 0/NULL"
            )


def _validate_style(con, errors: list[str]) -> None:
    tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    if "fact_player_style" not in tables:
        errors.append("missing fact_player_style")
        return
    dupes = con.execute(
        """
        SELECT season, player_code, COUNT(*) AS n
        FROM fact_player_style
        GROUP BY 1, 2
        HAVING COUNT(*) > 1
        """
    ).df()
    if not dupes.empty:
        errors.append(f"duplicate fact_player_style keys: {len(dupes)}")

    counts = con.execute(
        """
        SELECT season, COUNT(*) AS players
        FROM fact_player_style
        GROUP BY season
        ORDER BY season
        """
    ).df()
    _print_table("fact_player_style players per season", counts)

    if "fact_player_season_availability" in tables:
        movers = con.execute(
            """
            SELECT COUNT(*) FROM fact_player_style s
            LEFT JOIN fact_player_season_availability a
              ON s.season = a.season
             AND s.player_code = a.player_code
             AND a.grain = 'season'
            WHERE s.pass_share_of_team IS NOT NULL
              AND (a.spell_count IS NULL OR a.spell_count <> 1)
            """
        ).fetchone()[0]
        if movers:
            errors.append(
                f"{movers} pass_share_of_team values on multi-club/unknown spells"
            )

    if "fact_player_season_opta" in tables:
        thin = con.execute(
            """
            SELECT COUNT(*) FROM fact_player_style s
            JOIN fact_player_season_opta o USING (season, player_code)
            WHERE s.fwd_pass_share IS NOT NULL
              AND (o.total_pass IS NULL OR o.total_pass < 200)
            """
        ).fetchone()[0]
        if thin:
            errors.append(
                f"{thin} fwd_pass_share values where total_pass < 200"
            )
        thin_t = con.execute(
            """
            SELECT COUNT(*) FROM fact_player_style s
            JOIN fact_player_season_opta o USING (season, player_code)
            WHERE s.take_on_rate IS NOT NULL
              AND (o.touches IS NULL OR o.touches < 200)
            """
        ).fetchone()[0]
        if thin_t:
            errors.append(
                f"{thin_t} take_on_rate values where touches < 200"
            )


def _validate_clusters(con, errors: list[str]) -> None:
    from transform.style import CHOSEN_K, CLUSTER_META, EXCLUDE_FROM_CLUSTERING

    tables = {r[0] for r in con.execute("SHOW TABLES").fetchall()}
    if "dim_style_cluster" not in tables or "fact_player_cluster" not in tables:
        errors.append("missing dim_style_cluster or fact_player_cluster")
        return

    dim = con.execute(
        """
        SELECT cluster_id, label, description, n_player_seasons, note
        FROM dim_style_cluster
        ORDER BY cluster_id
        """
    ).df()
    if len(dim) != CHOSEN_K:
        errors.append(f"dim_style_cluster has {len(dim)} rows, expected {CHOSEN_K}")
    ids = [int(x) for x in dim["cluster_id"].tolist()] if not dim.empty else []
    if ids != list(range(CHOSEN_K)):
        errors.append(f"dim_style_cluster ids {ids}, expected 0..{CHOSEN_K - 1}")
    for cid, (label, desc) in CLUSTER_META.items():
        row = dim.loc[dim["cluster_id"] == cid]
        if row.empty:
            continue
        got_label = str(row.iloc[0]["label"])
        got_desc = str(row.iloc[0]["description"])
        if got_label != label:
            errors.append(f"cluster {cid} label {got_label!r}, expected {label!r}")
        if got_desc != desc:
            errors.append(f"cluster {cid} description mismatch")
    if dim["note"].isna().any() or (dim["note"].astype(str).str.strip() == "").any():
        errors.append("dim_style_cluster.note is blank")
    _print_table("dim_style_cluster", dim[["cluster_id", "label", "n_player_seasons"]])

    dupes = con.execute(
        """
        SELECT season, player_code, COUNT(*) AS n
        FROM fact_player_cluster
        GROUP BY 1, 2
        HAVING COUNT(*) > 1
        """
    ).df()
    if not dupes.empty:
        errors.append(f"duplicate fact_player_cluster keys: {len(dupes)}")

    unknown = con.execute(
        """
        SELECT COUNT(*) FROM fact_player_cluster f
        LEFT JOIN dim_style_cluster d USING (cluster_id)
        WHERE d.cluster_id IS NULL
        """
    ).fetchone()[0]
    if unknown:
        errors.append(f"{unknown} fact_player_cluster rows with unknown cluster_id")

    same = con.execute(
        """
        SELECT COUNT(*) FROM fact_player_cluster
        WHERE cluster_id = second_cluster_id
        """
    ).fetchone()[0]
    if same:
        errors.append(f"{same} rows where second_cluster_id equals cluster_id")

    inverted = con.execute(
        """
        SELECT COUNT(*) FROM fact_player_cluster
        WHERE second_distance + 1e-9 < distance
        """
    ).fetchone()[0]
    if inverted:
        errors.append(f"{inverted} rows where second_distance < distance")

    counts = con.execute(
        """
        SELECT season, COUNT(*) AS players
        FROM fact_player_cluster
        GROUP BY season
        ORDER BY season
        """
    ).df()
    _print_table("fact_player_cluster players per season", counts)

    n_mismatch = con.execute(
        """
        SELECT d.cluster_id, d.n_player_seasons, COUNT(f.player_code) AS n
        FROM dim_style_cluster d
        LEFT JOIN fact_player_cluster f USING (cluster_id)
        GROUP BY d.cluster_id, d.n_player_seasons
        HAVING d.n_player_seasons IS DISTINCT FROM COUNT(f.player_code)
        """
    ).df()
    if not n_mismatch.empty:
        errors.append("dim_style_cluster.n_player_seasons does not match fact counts")

    if "fact_player_style" in tables:
        excluded = con.execute(
            f"""
            SELECT COUNT(*) FROM fact_player_cluster c
            JOIN fact_player_style s USING (season, player_code)
            WHERE s.element_type IN ({", ".join(str(x) for x in sorted(EXCLUDE_FROM_CLUSTERING))})
            """
        ).fetchone()[0]
        if excluded:
            errors.append(
                f"{excluded} clustered rows are goalkeepers/managers"
            )




