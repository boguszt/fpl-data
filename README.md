# fpl-data

Personal Premier League / FPL ingest. Immutable raw snapshots, DuckDB + Parquet marts. No dashboard.

**raw is the asset** (unrecoverable). **marts are disposable** (deterministic from raw + transform). Marts are not in git.

## Setup

Python 3.12 and [uv](https://docs.astral.sh/uv/):

```bash
uv sync
make marts                  # rebuild parquet from raw/
# or: uv run python -m transform.build
```

After `git pull`, rebuild marts the same way. CI builds and validates them on every run but does not commit them.

`.venv/` is gitignored.

## Entry points

```bash
uv run python ingest/backfill.py   # one-shot history
uv run python ingest/update.py     # idempotent; safe to re-run
make marts                         # transform only
uv run python transform/spot_check.py
```

`backfill.py` pulls [vaastav/Fantasy-Premier-League](https://github.com/vaastav/Fantasy-Premier-League) seasons 2016-17 through 2026-27, snapshots the live FPL API, caches `element-summary` `history_past` at ~1 req/sec, then rebuilds marts.

`update.py` always writes a gzipped `bootstrap-static` snapshot (the timestamp is the data). Fixtures are re-fetched but only written when the payload bytes change. It pulls `event/{gw}/live/` for any gameweek whose bonus is finalised and not already on disk, refreshes current-season vaastav the same way, then rebuilds and validates marts.

## Layout

```
ingest/       # writes to raw/ only
transform/    # raw -> DuckDB -> marts/
raw/          # immutable, never overwritten; this is what git stores
marts/        # parquet; gitignored; rebuild with make marts
db/fpl.duckdb # local convenience; gitignored
DATA_DICTIONARY.md
```

`backfill.py` takes ~12 minutes on a cold run because `element-summary` is rate-limited to ~1 req/sec (then cached on disk). Re-runs skip existing raw files.

See `DATA_DICTIONARY.md` for table grains, the `xp` lookahead warning, and `fact_player_season` coverage bias.

## Cite

Historical CSVs: [vaastav/Fantasy-Premier-League](https://github.com/vaastav/Fantasy-Premier-League). Live data: [fantasy.premierleague.com/api](https://fantasy.premierleague.com/api/).
