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
| appearances, appearances_60_plus | Count of **matches** in this GW with minutes > 0 / ≥ 60, from per-fixture vaastav rows or live `explain`. A double-GW is 2 when the player played both, not 1. Not a GW-row count — `fact_player_gw` has already summed the two fixtures into one row. |
| value | Price that GW, in £0.1m. See `value_source`. |
| value_source | `vaastav` = per-GW CSV (price at that GW). `snapshot` = `now_cost` from a bootstrap snapshot taken **at or before** that GW's deadline (the price managers actually faced). NULL = no trustworthy source. **Do not use `value_source='snapshot'` rows for historical price analysis spanning GWs that finished before snapshotting began** — we never attach those. Going forward, a pre-deadline snapshot is the preferred source for the current season; vaastav is the fallback. |
| xp | Vaastav `xP` only. **Unsafe for modelling** (lookahead from post-GW `ep_this`). Never inferred. NULL when vaastav has no row. |

**2026-27 GW2–3 prices are gone.** Vaastav has not published those gameweeks (`merged_gw.csv` is GW1 only; `gw2.csv`/`gw3.csv` 404). Nobody was snapshotting `now_cost` before 2026-09-12, and the live API does not retain history. `value` and `xp` are NULL for those GWs unless vaastav later backfills (dated current-season refresh will pick that up). NULL is the honest answer.

Current-season vaastav CSVs mutate. They are re-fetched every `update.py`/`backfill.py` run and written to `raw/vaastav/{season}/{YYYY-MM-DD}/…` only when bytes change. Transform uses the latest dated copy. Finished seasons stay skip-if-present at `raw/vaastav/{season}/`.

**Upstream stall:** two finished gameweeks unpublished is not a lag pattern (a lag would omit the *latest* GW). If vaastav `data/2026-27/gws/` has still not moved by around GW6, [olbauday/FPL-Core-Insights](https://github.com/olbauday/FPL-Core-Insights) covers 2026/27 under `data/2026-2027/` and aligns on official FPL IDs — a drop-in fallback. Not wired up.

### Schema drift (do not treat NULL as zero)

- `expected_goals` / `expected_assists` / `expected_goal_involvements` / `expected_goals_conceded`: from 2022-23. 2022-23 itself is partial (scraper started mid-season).
- `starts`: recent.
- `own_goals`, `penalties_missed`, `penalties_saved`: in vaastav `merged_gw` and live stats; now on `fact_player_gw` (NULL when the source season lacks them).
- `tackles`, `recoveries`, `clearances_blocks_interceptions`: present 2016-17–2018-19, then absent, then back from 2025-26.
- `defensive_contribution`: from 2025-26.

## `fact_player_fixture`

Grain: `(season, gw, player_code, fixture_id)`. One row per **match**, not per gameweek.

Built from distinct vaastav `merged_gw` fixture rows (before DGW collapse) and, when a live file exists, one row per `explain[]` block. A double-GW is two rows with the same `gw` and different `fixture_id`. Opponent / `was_home` come from that fixture. Stats that live `explain` does not list are NULL on a DGW split — do not copy the GW total onto both rows.

Blank gameweeks (the player's club had no fixture) are **not** stored here. `web/data/matchlogs_{season}.json` synthesises them from `fact_fixture` with `blank: true` and null minutes / points / metrics.

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

## Derived metrics

Rebuilt from `fact_player_gw` / `fact_fixture` every `make marts`. The static player table ranks client-side against the current filtered set.

Each counting stat has `*_total` and `*_p90` (`total / minutes * 90`). Per-90 rates also have an empirical-Bayes shrink `{metric}_adj90` toward the minutes-weighted `(season, element_type)` mean, with prior strength `k` nineties stored in `dim_shrinkage`. Share metrics also have `*_share` and are **not** shrunk.

### `fact_team_gw`

Grain: `(season, gw, team_code)`.

Sums of every `fact_player_gw` row with that GW `team_code`: `xg`, `xa`, `xgi`, `xgc`, `goals`, `assists`, `minutes`, `tackles`, `recoveries`, `clearances_blocks_interceptions`, `defensive_contribution`, `bps`, `saves`.

`matches_played`: distinct played fixtures for that team in that GW (1 normally, 2 in a DGW). Taken from `fact_fixture` rows with a `result`. If fixture rows are missing but the team recorded minutes, falls back to 1.

Requires `fact_player_gw.team_code`. That column is populated from 2020-21; 2016-17–2019-20 rows currently have NULL `team_code`, so this mart (and spell shares) start in 2020-21.

### `fact_player_season_metrics`

Two grains in one table, distinguished by `grain` and `team_code`:

| grain | key | `team_code` | `spell_count` |
| --- | --- | --- | --- |
| `spell` | `(season, player_code, team_code)` | club for that spell | number of clubs that season |
| `season` | `(season, player_code)` | NULL | same |

A mid-season transfer gets two spell rows plus one season row. Spell totals must sum to the season totals. Shares are **not** season-level player/team over all 38: they are `SUM(player stat in GWs at club X) / SUM(team X stat in those same GWs)`, using registration GWs (including 0-minute benches). The season-grain share is a **minutes-weighted blend** of the spell shares.

`element_type` is that season's position from `players_raw` / current bootstrap, not `dim_player.current_element_type`.

Goalkeeping metrics (`saves`, `penalties_saved`) have adj90 only for `element_type = 1`; outfield rows are NULL.

Direction: `data/metric_direction.csv` (`dim_metric` in the marts). `direction = lower` means fewer is better (`goals_conceded`, `xgc`, `yellow_cards`, `red_cards`, `own_goals`, `penalties_missed`). The player table uses this for rank (1 = good).

### `fact_player_season_availability`

Same spell / season grain as the metrics table.

`appearances` = matches with minutes > 0 (not distinct gameweeks). `appearances_60_plus` = matches of at least 60 minutes. A double gameweek of 90 and 75 is two appearances and two 60+ matches. `minutes_per_appearance` = minutes / appearances.

`team_minutes_available` = `90 * SUM(matches_played)` of that club over GWs the player was registered there. `minutes_share` = minutes / team_minutes_available.

Set-piece orders are from `dim_setpieces` (latest bootstrap `as_of`). They are **not** historical — a 2019 spell still shows today's penalty order.

### `dim_metric`

Copy of `data/metric_direction.csv`: `metric_name`, `direction` (`higher` / `lower`), `display_name`, `group` (Attacking / Defensive / Goalkeeping / FPL), `applies_to_positions`. The player table is driven entirely from this table.

### `dim_shrinkage`

Grain: `(season, element_type, metric)`.

`k` is prior strength in nineties for `{metric}_adj90`. Fitted once per `(metric, element_type)` from a split-half correlation of GW-level rates (`method = split_half`): split each player-season's gameweeks at random, correlate the two half-rates, then `k = n_half * (1 - r) / r` — the sample size at which signal equals noise. If fewer than 20 usable pairs or `r` is not in `(0, 1]`, `k = 10` and `method = fallback`. The same `k` is stored on every season row. Pool mean for the shrink is still `(season, element_type)`.

### `dim_region`

Copy of `data/region_lookup.csv`: `region_id`, `country_name`, `iso_alpha2`, `iso_alpha3`.

FPL `region` is a bare integer on bootstrap / `dim_player`. Names come from FPL `/api/regions/` (the dictionary for those ids), ISO 3166-1 used to fill a blank name (Russia, id 178) and to confirm codes. Home nations use FIFA-style codes (`ENG`/`SCO`/`WAL`/`NIR`), not ISO-3166. Ids that cannot be named stay NULL rather than guessed. Join `dim_player.region = dim_region.region_id`.

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
