# raw/

Immutable snapshots. Never overwrite a file; a new timestamp is a new fact.

## Held here

See `DATA_DICTIONARY.md` for the full layout. `raw/bootstrap/` is our own `bootstrap-static` pulls (gzipped, every run).

## Not held here: Randdalf/fplcache

[Randdalf/fplcache](https://github.com/Randdalf/fplcache) is a public archive of `bootstrap-static` at six-hour intervals, LZMA-compressed (`cache/{year}/{month}/{day}/{time}.json.xz`). It is ~850 MB and is its own repo.

Do **not** copy it into `raw/`. Clone it shallowly outside this tree:

```
git clone --depth 1 https://github.com/Randdalf/fplcache.git ../fplcache-src
```

Or set `FPLCACHE_DIR`. `make marts` streams those `.xz` files into `snap_player_day` with `source='fplcache'`. Rows with `source='own'` came from `raw/bootstrap/` in this repo. The verbatim fplcache JSON is not stored locally in fpl-data.

If the clone is missing, marts still build; `snap_player_day` then has only `own` rows.

## Pulse Opta (`raw/plstats/`)

Undocumented `footballapi.pulselive.com` season totals (`Origin: https://www.premierleague.com`). Closed seasons are written once and never overwritten. The current season is fetched on both crons, dated, and only stored when the payload bytes change. Census is ranked appearances, not `/football/players`. `raw/feed_fetch/` records when each feed was last successfully fetched.
