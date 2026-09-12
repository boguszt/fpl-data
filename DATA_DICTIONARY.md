# Data dictionary

Grains, keys, and known traps. All costs are FPL tenths of a million (`50` = £5.0m).

## Identity

FPL `id` (element id, team id) is **per-season and reused**. `code` is stable across seasons. Cross-season tables key on `code`. Per-season `id` lives in `dim_season_map`.

`opta_code` is also stable when present; early `players_raw.csv` does not have it, so it is not the primary key.

## `dim_player`

Grain: `code`.

| column | notes |
| --- | --- |
| code | Stable FPL player code |
| opta_code | Stable when present |
| first_name, second_name, web_name | Latest known |
| birth_date, region | From later bootstrap / `players_raw`; NULL historically |
| current_element_type | Latest bootstrap: 1=GK, 2=DEF, 3=MID, 4=FWD |
| current_team_code | Latest bootstrap team. **Not** GW-accurate |

## `dim_team`

Grain: `(season, code)`.

| column | notes |
| --- | --- |
| code | Stable team code |
| id | That season's 1–20 FPL team id |
| name, short_name | Season-correct; `short_name` NULL when only `master_team_list` exists (e.g. 2016-17) |

## `dim_season_map`

Grain: `(season, element_id)`.

| column | notes |
| --- | --- |
| season | `YYYY-YY` |
| element_id | Per-season FPL player `id` |
| player_code | Stable `code` |
| team_id, team_code | Last-known / `players_raw` / latest bootstrap for that season. **Not GW-accurate.** Mid-season transfers are not represented here. Join facts on `player_code`; take `team_code` from `fact_player_gw`. |

## `fact_player_gw`

Grain: `(season, gw, player_code)`. Double-gameweeks are collapsed (stats summed). `opponent` and `was_home` are NULL when a player has more than one fixture in the GW.

| column | notes |
| --- | --- |
| team_code | Club **that gameweek**. Sourced from vaastav `merged_gw.team` (name → code), else from the latest bootstrap snapshot. Never from `dim_season_map`. |
| opponent | Opponent team **code** |
| was_home | |
| minutes, starts, goals, assists, … | Live `event/{gw}/live/` stats win for a `(season, gw)` when that file exists; otherwise vaastav. Missing historical columns are NULL, not zero. |
| value | Price that GW, in £0.1m. See `value_source`. |
| value_source | `vaastav` = per-GW CSV (price at that GW). `snapshot` = `now_cost` from a bootstrap snapshot taken **at or before** that GW's deadline (the price managers actually faced). NULL = no trustworthy source. **Do not use `value_source='snapshot'` rows for historical price analysis spanning GWs that finished before snapshotting began** — we never attach those. Going forward, a pre-deadline snapshot is the preferred source for the current season; vaastav is the fallback. |
| xp | Vaastav `xP` only. **Unsafe for modelling** (lookahead from post-GW `ep_this`). Never inferred. NULL when vaastav has no row. |

**2026-27 GW2–3 prices are gone.** Vaastav has not published those gameweeks (`merged_gw.csv` is GW1 only; `gw2.csv`/`gw3.csv` 404). Nobody was snapshotting `now_cost` before 2026-09-12, and the live API does not retain history. `value` and `xp` are NULL for those GWs unless vaastav later backfills (dated current-season refresh will pick that up). NULL is the honest answer.

Current-season vaastav CSVs mutate. They are re-fetched every `update.py`/`backfill.py` run and written to `raw/vaastav/{season}/{YYYY-MM-DD}/…` only when bytes change. Transform uses the latest dated copy. Finished seasons stay skip-if-present at `raw/vaastav/{season}/`.

**Upstream stall:** two finished gameweeks unpublished is not a lag pattern (a lag would omit the *latest* GW). If vaastav `data/2026-27/gws/` has still not moved by around GW6, [olbauday/FPL-Core-Insights](https://github.com/olbauday/FPL-Core-Insights) covers 2026/27 under `data/2026-2027/` and aligns on official FPL IDs — a drop-in fallback. Not wired up.

### Schema drift (do not treat NULL as zero)

- `expected_goals` / `expected_assists` / `expected_goal_involvements` / `expected_goals_conceded`: from 2022-23. 2022-23 itself is partial (scraper started mid-season).
- `starts`: recent.
- `tackles`, `recoveries`, `clearances_blocks_interceptions`: present 2016-17–2018-19, then absent, then back from 2025-26.
- `defensive_contribution`: from 2025-26.

## `fact_fixture`

Grain: `(season, fixture_id)`.

Home/away team **codes**, kickoff, `result` as `home-away` when played, FDR `home_difficulty` / `away_difficulty` (NULL when derived from `merged_gw` because no `fixtures.csv` exists, e.g. 2016-17).

## `snap_player_day`

Grain: `(snapshot_ts, player_code)`.

One row per player per gzipped `bootstrap-static` pull. These fields mutate continuously and are unrecoverable if not snapshotted: `now_cost`, `selected_by_percent`, `transfers_in_event`, `transfers_out_event`, `status`, `chance_of_playing_next_round`, `news`.

Raw path: `raw/bootstrap/{YYYY-MM-DD}/{HHMM}.json.gz`. Never overwritten. Gzip is required so git stays tractable.

## `dim_setpieces`

Grain: `player_code` as of the latest bootstrap (`as_of` date). Penalty / direct FK / corner order. NULL means not on that list.

## `fact_player_season`

Grain: `(player_code, season)`.

From `element-summary` `history_past` only.

**Coverage bias:** backfill requests `element-summary` only for players in the *current* `bootstrap-static`. This table is the current FPL pool's past-season totals, not everyone who has ever played. Retired and dropped players are absent. GW-level history for all players is in `fact_player_gw` via vaastav.

## Live join path

`GET event/{gw}/live/` elements look like:

```json
{
  "id": 1,
  "stats": { "minutes": 90, "total_points": 6 },
  "explain": [{ "fixture": 1, "stats": [{"identifier": "minutes", "points": 2, "value": 90}] }]
}
```

`explain[].fixture` is the fixture id. Opponent / `was_home` come from that fixture plus the **GW** `team_code`, not from `dim_season_map`.

## Raw layout

| path | source |
| --- | --- |
| `raw/vaastav/{season}/merged_gw.csv` | vaastav, finished seasons (immutable) |
| `raw/vaastav/{season}/{YYYY-MM-DD}/merged_gw.csv` | vaastav current season (dated; latest wins) |
| `raw/vaastav/{season}/players_raw.csv` | vaastav (finished seasons) |
| `raw/vaastav/{season}/fixtures.csv` | vaastav (404 OK for early seasons) |
| `raw/vaastav/{season}/teams.csv` | vaastav (404 OK) |
| `raw/vaastav/master_team_list.csv` | vaastav |
| `raw/vaastav/understat/{season}/` | vaastav Understat dump (`understat_player.csv`, per-player match files, `id_dict.csv` when published). **Present and unprocessed** — no marts, no joins. Coverage **stops at 2024-25**; 2025-26 and 2026-27 have no `understat/` upstream. The FPL↔Understat ID mapping therefore omits anyone who arrived in the last two seasons. Next phase starts by extending that map, not by using it as-is. A few files 404 (notably Kanté listings with HTML-entity filenames); that is URL-encoding on accented/apostrophe paths, retriable later, not worth blocking on. |
| `raw/fixtures/{season}/{YYYY-MM-DD}/{HHMM}.json` | FPL fixtures. Dated and immutable, but **content-hash deduped**: a new file is written only when the payload changes (reschedules, a handful of times per season). |
| `raw/bootstrap/{YYYY-MM-DD}/{HHMM}.json.gz` | FPL bootstrap-static. Written **every run**, even on identical bytes — the timestamp is the data. Never deduped. |
| `raw/event-status/{YYYY-MM-DD}/{HHMM}.json` | FPL event-status |
| `raw/live/{season}/gw{N}.json` | FPL event/{gw}/live |
| `raw/element-summary/{code}.json` | FPL element-summary (cached by stable code) |

## Cite

Historical CSVs: [vaastav/Fantasy-Premier-League](https://github.com/vaastav/Fantasy-Premier-League), including their `xP` caveat. Live: [fantasy.premierleague.com/api](https://fantasy.premierleague.com/api/).
