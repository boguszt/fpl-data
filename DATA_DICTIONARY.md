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
| opta_code | FPL `p{code}` stripped to int; filled from `code` when the source omitted it |
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

When a `raw/live/{season}/gw{N}.json` file exists, each played match-log row includes FPL's per-fixture `explain` array (identifier, FPL label from bootstrap `element_stats`, value, points) in FPL's own order. Zero-point components FPL returned are kept. The key is omitted entirely for GWs with no live file (vaastav-only seasons) rather than written as `[]`. Double-GWs keep one array per fixture row; they are not merged. Currently only 2026-27 has live files.

## `data/metric_register.csv`

Generated by `transform/metric_register.py` from the marts, never hand-maintained. One row per `(id, grain, context, feed)`. Canonical is per `(metric, grain, context)`: Fantasy uses FPL for anything that scored points; season football measurements use Opta; xG family is FPL because Opta has none; per-match is FPL; price/ownership is fplcache. Both feeds may store the same action under distinct names — they must not both be `canonical=1` at the same grain and context.

Every Opta id is prefixed `o_` (`o_total_pass`, `o_total_tackle`, …) so FPL keys (`tackles`, `goals`, …) cannot collide at the data layer. Shipped Opta season counts land on `opta_{season}.json` as `{id}_total`; derived ratios (`o_pass_accuracy`, …) land as `{id}` already computed. Null keys are omitted (missing and null are the same). Nothing Opta goes into `players_{season}.json` or `matchlogs_{season}.json`. `manifest.json` has `opta: true` per season when that side file exists.

Tiers: `core` ships and is the default table; `extended` ships (Opta season metrics, style ratios, availability, price/own, derived rates and concepts); `archive` is remaining Opta fields and `snap_player_day` extras — mart only, never metric values in `web/data`. `web/data/metrics_register.json` is the same CSV.

Register fields the UI renders: `label`, `feed`, `raw_field`, `definition`, `derived_from`, `grain`, `context`, `first_season`, `last_season` (blank if current), `coverage_note`, `caveat`, `group`, `direction` (1 or −1), `tier`, `used_in`. Groups include Passing, Territory and Duels as well as Attacking / Defensive / Goalkeeping / Discipline / Fantasy / Playing time.

Derived concepts with their own rows: `adjusted_per_90`, `percentile`, `share_of_team`, `style_cluster`.

Do not duplicate metric descriptions in `index.html`.

`web/data/status.json` is rewritten every export. `generated_at` is the export clock; each feed's `as_of` is the last successful **fetch** of that feed (not the file write, not the job start). An Opta pull that is a no-op because bytes were unchanged still updates `as_of`. Style `as_of` is when `clusters.json` last changed (fitted model, rebuilt on demand). CI fails if `fpl` / `opta` / `vaastav` `as_of` is more than 30 hours old.

## `fact_fixture`

Grain: `(season, fixture_id)`.

Home/away team **codes**, kickoff, `result` as `home-away` when played, FDR `home_difficulty` / `away_difficulty` (NULL when derived from `merged_gw` because no `fixtures.csv` exists, e.g. 2016-17).

## `snap_player_day`

Grain: `(snapshot_ts, player_code)`.

One row per player per `bootstrap-static` snapshot. These fields mutate continuously and are unrecoverable if not snapshotted: `now_cost`, `selected_by_percent`, `transfers_in_event`, `transfers_out_event`, `status`, `chance_of_playing_next_round`, `news`.

Grain: `(snapshot_ts, player_code, source)`. Identity is always FPL `code`, never `id` — ids are reused across seasons.

`source` is `own` or `fplcache`:

| source | origin |
| --- | --- |
| `own` | `raw/bootstrap/{YYYY-MM-DD}/{HHMM}.json.gz` in this repo |
| `fplcache` | [Randdalf/fplcache](https://github.com/Randdalf/fplcache) `cache/{year}/{month}/{day}/{time}.json.xz` |

The fplcache files are **not** copied into `raw/` (~850 MB compressed). Clone that repo shallowly outside this tree (`../fplcache-src`, or set `FPLCACHE_DIR`) and rebuild marts. If the clone is absent, `snap_player_day` contains `own` rows only. Same-day own and fplcache snapshots are both kept — different times; the timestamp is the data.

`season` is taken from the bootstrap `events` array (first deadline), not the file date. Fields that did not exist yet (`expected_*`, `defensive_contribution`, `opta_code`) are omitted here; other missing attributes are NULL, never 0.

This mart is exported into `web/data` from 2021-22 onward (fplcache coverage starts 2021-04-18; 2016-17 through 2020-21 have no `pricehistory_*.json` and NULL price columns, never zeros):

- `players_{season}.json`: `price_now` / `price_start` / `price_delta` (millions, 1 dp; `now_cost / 10`) and `own_now` / `own_7d` / `own_30d` (percentage points, 1 dp). Completed seasons are end-of-season values. `own_7d` / `own_30d` are NULL unless a snapshot exists within 48 hours of T−7d / T−30d.
- `matchlogs_{season}.json`: `price` at that GW's `deadline_time` and `price_delta` vs the previous GW deadline. Snapshot at or immediately before the deadline; both NULL if none within 48 hours — no interpolate or carry-forward. Deadlines come from bootstrap `events`, not fixture kickoff.
- `pricehistory_{season}.json`: object keyed by `player_code` (string). `start` is the season's first snapshot date; `price` is a change log `[[days_since_start, new_price], ...]` (step function, entry at day 0 when present); `own` is a daily sample (midday-UTC closest snapshot, `null` for missing days so index = day offset).

## `dim_setpieces`

Grain: `player_code` as of the latest bootstrap (`as_of` date). Penalty / direct FK / corner order. NULL means not on that list.

## `fact_player_season_opta`

Grain: `(season, pulse_id)`.

Premier League Pulse/Opta season totals from `raw/plstats/`. FPL `code` is the Opta number (`p{code}` = Pulse `altIds.opta`) **when the player exists in `dim_player`**. `player_code` is filled only on that match and is NULL for the pre-FPL archive. Pulse `id` is `pulse_id` and is the grain for every season, including 1992-93–2015-16.

Do not put Pulse-only people on `dim_player` — that table is the FPL universe. They live on `dim_player_opta`.

Every stat name the API returned is a column (union across seasons; earlier years NULL). Missing fields are not an error: 1990s payloads are sparse. Derived, NULL when the denominator is 0, never a fake zero:

- `pass_accuracy` = `accurate_pass / total_pass`
- `cross_accuracy` = `accurate_cross / total_cross`
- `duel_win_pct` = `duel_won / (duel_won + duel_lost)`
- `aerial_win_pct` = `aerial_won / (aerial_won + aerial_lost)`
- `shot_accuracy` = `ontarget_scoring_att / total_scoring_att`

Census is ranked appearances, not `/football/players`. Closed seasons are a one-shot archive from **1992-93**; the current season refreshes on both GitHub crons (06:00 and 18:00 UTC). Always fetch; write a new dated dump only when bytes change. A second snapshot on the same UTC day lands at `{YYYY-MM-DD}/{HHMM}/`. Coverage boundaries (first season any player has the field, first season ≥50% of that year's squad has it, last season) are in `data/opta_coverage.csv`.

Shipped core/extended Opta fields are exported as `web/data/opta_{season}.json` for FPL seasons only, an object keyed by `player_code` (string) in the same shape as `style_{season}.json`. Counts are `o_{field}_total`; derived ratios are `o_{field}`. Null keys are omitted. Archive Opta columns stay mart-only. Nothing Opta is written to `players_{season}.json` or match logs. Pre-2016-17 seasons are not in `manifest.json`.

## `dim_player_opta`

Grain: `pulse_id`.

Players who appear in the Pulse archive but never in `dim_player` (no FPL row). `opta_code` is Pulse `altIds.opta` stripped of the leading `p`. `name` is the latest display name we saw. `first_season` / `last_season` span the archive, not FPL.

This is identity for the historical dump, not a join key onto FPL tables. Do not invent a mapping.

## `fact_player_style`

Grain: `(season, player_code)`.

Scale-free Opta style features from `data/style_features.csv`. Every feature is a ratio — never a volume or a percentile — so clustering describes **how** a player plays, not how good they are.

Floors: NULL when the denominator is 0, when `total_pass < 200` (passing family / pass share of team), or when `touches < 200` (territory, on-the-ball, shots-per-touch, touch share of team). Pulse omits a counting stat when it is 0; those are filled as 0 once the field has appeared for at least half a prior-or-current season's squad (so an in-progress season with few through-balls still gets zeros, not NULLs). Leave NULL when the schema is absent that year — carries in 2020-21 and 2022-23 stay NULL; we do not back-fill from 2024-25.

Team share (`pass_share_of_team`, `shot_share_of_team`, `touch_share_of_team`) is computed only for single-club seasons (`spell_count = 1` on `fact_player_season_availability`). The Pulse row is one season total; a mid-season transfer cannot be split across clubs, so movers are NULL, never approximated. The team denominator is the sum of that Opta count over single-club teammates. `spell_count` itself only exists from 2020-21 (`fact_player_gw.team_code`); earlier seasons have NULL team share.

`{feature}_z` is a z-score within `(season, element_type)` so a centre-back is not compared with a winger on clearance share. `element_type` is that season's FPL position (1=GK, 2=DEF, 3=MID, 4=FWD).

Accuracy / success-rate features (`pass_accuracy`, `aerial_win_pct`, `take_on_success`) are stored and z-scored but `in_clustering = 0` — they measure quality, not shape.

**Fit boundary:** do not cluster 2016-17–2018-19, and do not backfill those years with a reduced feature set. Carries / progressive carries / touches in the final third first appear in Pulse in 2019-20, but they are only populated for essentially the whole squad from **2024-25**. Complete-case k-means on the clustering vector is therefore 2024-25 onward.

k = 9, fitted once on the pooled complete cases, then every row assigned against that single model (nearest and second-nearest). Goalkeepers and managers are **excluded** from the fit, not fitted as a second model. Their `fact_player_style` rows exist but they have no row in `fact_player_cluster`.

- Clusters 3 (Aerial defender) and 8 (Ball-playing defender) are separated largely by team pass and touch share, so the split partly reflects team possession role rather than individual technique. Do not present it as purely a player attribute.
- The model is fitted on 682 player-seasons, 658 of them from 2024-25 and 2025-26, because carries and final-third touches are not populated league-wide before 2024-25. It is effectively a two-season model and will deepen by one season per year.
- Goalkeepers and managers are excluded from the fit. Their style rows exist but carry no cluster.

Web export: `web/data/clusters.json` (feature order, `features_meta` with label / format / per-season z-pool counts, labels, centroid z-vector per cluster) and `web/data/style_{season}.json` (only seasons with assignments). Each player is `{z, raw, c, d, c2, d2}` — `z` is the clustering z-vector and `raw` the underlying ratio, both in `clusters.json.features` order. Volumes stay on `opta_{season}.json`; they are not folded into the style file.

## `dim_style_cluster`

Grain: `cluster_id` (0–8).

`label`, `description`, `n_player_seasons`, `note` (the three bullets above, on every row), plus one column per clustering feature holding the centroid in z-space.

## `fact_player_cluster`

Grain: `(season, player_code)`.

`cluster_id`, `distance` (Euclidean to the winning centroid), `second_cluster_id`, `second_distance`. A player 0.9 from one centroid and 0.95 from another is genuinely both; the frontend should show that. Outfielders who do not clear the complete-case vector are absent, as are keepers and managers.

## `fact_player_season`

Grain: `(player_code, season)`.

From `element-summary` `history_past` only.

**Coverage bias:** backfill requests `element-summary` only for players in the *current* `bootstrap-static`. This table is the current FPL pool's past-season totals, not everyone who has ever played. Retired and dropped players are absent. GW-level history for all players is in `fact_player_gw` via vaastav.

## Derived metrics

Rebuilt from `fact_player_gw` / `fact_fixture` every `make marts`. The static player table ranks client-side against the current filtered set.

Each counting stat has `*_total` and `*_p90` (`total / minutes * 90`). Per-90 rates also have an empirical-Bayes shrink `{metric}_adj90` toward the minutes-weighted `(season, element_type)` mean, with prior strength `k` nineties stored in `dim_shrinkage`. Share metrics also have `*_share` and are **not** shrunk.

### `fact_team_gw`

Grain: `(season, gw, team_code)`.

Player-sum of things a player **does**: `xg`, `xa`, `xgi`, `goals`, `assists`, `minutes`, `tackles`, `recoveries`, `clearances_blocks_interceptions`, `defensive_contribution`, `bps`, `saves`. These are the share denominators.

Things measured **about the team** while a player is on the pitch cannot be summed across the squad:

- `goals_conceded` / `clean_sheets` come from `fact_fixture` scorelines
- `xgc` is the opponent's attacking xG in those fixtures (player-sum xG of the other side), never FPL's per-player xGC

`matches_played`: distinct played fixtures for that team in that GW (1 normally, 2 in a DGW). Taken from `fact_fixture` rows with a `result`. If fixture rows are missing but the team recorded minutes, falls back to 1.

Requires `fact_player_gw.team_code`. That column is populated from 2020-21; 2016-17–2019-20 have no `fact_team_gw` rows.

Web: `web/data/teams_{season}.json` (2020-21 onward, keyed by `team_code`) and `web/data/fixtures_{season}.json` (every season, played and unplayed). `players_{season}.json` / `matchlogs_{season}.json` / `opta_{season}.json` cover FPL seasons from **2016-17**. Club-share columns (`{key}_team`) are NULL before 2020-21; xG family NULL before 2022-23; defensive contribution NULL before 2025-26. No `pricehistory_*.json` or `style_*.json` before 2021-22 / clustered seasons. Both team and fixture files fold under the FPL fetch in `status.json`. Current-season fixtures rewrite when a result or reschedule changes the payload; past seasons are frozen.

### `fact_player_season_metrics`

Two grains in one table, distinguished by `grain` and `team_code`:

| grain | key | `team_code` | `spell_count` |
| --- | --- | --- | --- |
| `spell` | `(season, player_code, team_code)` | club for that spell | number of clubs that season |
| `season` | `(season, player_code)` | NULL | same |

A mid-season transfer gets two spell rows plus one season row. Spell totals must sum to the season totals. Shares are **not** season-level player/team over all 38: they are `SUM(player stat in GWs at club X) / SUM(team X stat in those same GWs)`, using registration GWs (including 0-minute benches). The season-grain share is a **minutes-weighted blend** of the spell shares.

`fact_player_gw.team_code` is populated from 2020-21. 2016-17–2019-20 still have season-grain rows (totals, per-90, adj90); they have no spell rows, `spell_count` is NULL, and every `*_share` is NULL. Missing team is not a reason to drop the season.

`element_type` is that season's position from `players_raw` / current bootstrap, not `dim_player.current_element_type`.

Goalkeeping metrics (`saves`, `penalties_saved`) have adj90 only for `element_type = 1`; outfield rows are NULL.

Direction: `data/metric_direction.csv` (`dim_metric` in the marts). `direction = lower` means fewer is better (`goals_conceded`, `xgc`, `yellow_cards`, `red_cards`, `own_goals`, `penalties_missed`). The player table uses this for rank (1 = good).

### `fact_player_season_availability`

Same spell / season grain as the metrics table. 2016-17–2019-20 are season-grain only: `team_minutes_available` and `minutes_share` are NULL.

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
| `raw/plstats/{season}/appearances.json` | Pulse ranked appearances (verbatim). Closed seasons, 1992-93 onward. |
| `raw/plstats/{season}/players/{pulse_id}.json` | Pulse `/stats/player/{id}` season totals. Closed seasons; skip if present. |
| `raw/plstats/{season}/{YYYY-MM-DD}/…` | Current-season dated snapshot. Always fetched on both crons; written only when bytes change. Same-day second dump: `{YYYY-MM-DD}/{HHMM}/`. |
| `raw/feed_fetch/{YYYY-MM-DD}/{HHMM}.json` | Per-feed `as_of` (last successful HTTP fetch, including no-op unchanged pulls). Written every `ingest/update.py` run. |

## Cite

Historical CSVs: [vaastav/Fantasy-Premier-League](https://github.com/vaastav/Fantasy-Premier-League), including their `xP` caveat. Live: [fantasy.premierleague.com/api](https://fantasy.premierleague.com/api/). Price/ownership history also uses [Randdalf/fplcache](https://github.com/Randdalf/fplcache) for `snap_player_day` (`source='fplcache'`); the xz archive is not stored in this repo. Season Opta totals: undocumented [footballapi.pulselive.com](https://footballapi.pulselive.com/football/competitions/1/compseasons) (`Origin: https://www.premierleague.com`).
