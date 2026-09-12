from __future__ import annotations

import pandas as pd


class MartValidationError(AssertionError):
    pass


def _print_table(title: str, df: pd.DataFrame) -> None:
    print(f"\n== {title} ==")
    print(df.to_string(index=False))


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
        errors.append("GW with >900 players minutes>0 — likely a join explosion")

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

    if errors:
        raise MartValidationError("mart validation failed:\n- " + "\n- ".join(errors))
    print("\nvalidation passed")
