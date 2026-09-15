# fpl-data

Personal Premier League / FPL ingest. Immutable raw snapshots, DuckDB + Parquet marts, static player table.

**raw is the asset** (unrecoverable). **marts are disposable** (deterministic from raw + transform). Marts are not in git. The player table reads committed JSON in `web/data/`, rebuilt from marts.

## Setup

Python 3.12 and [uv](https://docs.astral.sh/uv/):

```bash
uv sync
make marts                  # rebuild parquet from raw/, then web/data JSON
# or: uv run python -m transform.build && uv run python -m transform.export_web
```

`make marts` rebuilds every mart from `raw/`, including derived metrics, then exports `web/data/*.json`. CI does the same and commits only `raw/` and `web/data/` — never parquet.

`.venv/` is gitignored. Lookups in `data/` (metric direction, region names) are versioned — they are not marts.

## Player table

Static HTML in `web/`. No server. Serve the folder locally, or load it after `web/data` is on GitHub.

```bash
uv sync
make marts                            # if parquet / JSON is missing
python -m http.server 8000 --directory web
# then http://localhost:8000/?local=1
```

`?local=1` reads `web/data/` from disk. Without it, the page fetches JSON from GitHub and caches season files in IndexedDB. Default view is 2025-26, minutes ≥ 600.

## Entry points

```bash
uv run python ingest/backfill.py   # one-shot history
uv run python ingest/update.py     # idempotent; safe to re-run
uv run python -m ingest.plstats    # Pulse Opta season-stat archive (2016-17 onward)
make marts                         # transform + web JSON
make web                           # JSON only, from existing marts
python -m http.server 8000 --directory web
uv run python transform/spot_check.py
```

`backfill.py` pulls [vaastav/Fantasy-Premier-League](https://github.com/vaastav/Fantasy-Premier-League) seasons 2016-17 through 2026-27, snapshots the live FPL API, caches `element-summary` `history_past` at ~1 req/sec, then rebuilds marts.

`update.py` always writes a gzipped `bootstrap-static` snapshot (the timestamp is the data). Fixtures are re-fetched but only written when the payload bytes change. It pulls `event/{gw}/live/` for any gameweek whose bonus is finalised and not already on disk, and refreshes current-season vaastav the same way. Both GitHub crons (06:00 and 18:00 UTC) also pull Pulse Opta season totals for the current season (`FPL_PLSTATS=1`); a new `raw/plstats` dump is written only when bytes change. Each run stamps `raw/feed_fetch/` with per-feed fetch times. The GitHub Action then rebuilds marts, exports `web/data` (including a rewritten `status.json`), and commits JSON whose bytes changed. Export fails in CI if a scheduled feed's `as_of` is more than 30 hours old.

## Layout

```
ingest/       # writes to raw/ only
transform/    # raw -> DuckDB -> marts/ -> web/data JSON
raw/          # immutable, never overwritten; this is what git stores
marts/        # parquet; gitignored; rebuild with make marts
db/fpl.duckdb # local convenience; gitignored
web/          # static player table; web/data JSON is committed
data/         # metric_direction.csv, region_lookup.csv (versioned lookups)
DATA_DICTIONARY.md
```

`backfill.py` takes ~12 minutes on a cold run because `element-summary` is rate-limited to ~1 req/sec (then cached on disk). Re-runs skip existing raw files.

See `DATA_DICTIONARY.md` for table grains, the `xp` lookahead warning, `fact_player_season` coverage bias, and the derived metrics layer (shares, per-90, shrinkage).

## Cite

Historical CSVs: [vaastav/Fantasy-Premier-League](https://github.com/vaastav/Fantasy-Premier-League). Live data: [fantasy.premierleague.com/api](https://fantasy.premierleague.com/api/).
