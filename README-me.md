# The FPL player table

This folder holds a Premier League player table and the data behind it. You do not need to know how the code works.

## How to open it

Double-click `start-fpl.bat`.

That file:

1. Updates this folder from GitHub if it can.
2. Starts a small local web server.
3. Opens your usual browser at http://localhost:8000/ (or 8001 / 8002 if 8000 is already taken).

Leave the black window open while you use the site. Closing that window stops the server.

If it says Python is not on PATH, install Python from [python.org](https://www.python.org/downloads/) and tick **Add python.exe to PATH**, then try again.

If it says ports 8000–8002 are in use, something else on this computer is already using those addresses. Close that program and try again.

## If the site shows old data

1. Close the black window.
2. Double-click `start-fpl.bat` again. The first thing it does is download the latest files from GitHub into this folder.
3. If the page still looks stale, press Ctrl+F5 in the browser (a hard refresh).

The numbers come from the `web/data` folder on this computer. GitHub is only the source those files are copied from.

## Are the automatic updates running?

Twice a day GitHub tries to fetch new FPL data (around 6:00 and 18:00 UTC, often a few hours late).

To check:

1. Open https://github.com/boguszt/fpl-data/actions
2. Open the workflow named **FPL update**.
3. Look at the latest runs. The **Event** column should sometimes say `schedule`, not only `workflow_dispatch`.
4. A green tick means that run finished. You should also see recent commits on https://github.com/boguszt/fpl-data/commits/master from `github-actions[bot]` — usually `chore: fpl raw snapshots` and, when the table data changed, `chore: web player-table json`.

If the last successful `schedule` run is more than a couple of days old, the automatic updates are not happening.

You can also run the workflow by hand on that Actions page with **Run workflow**.

## Where the data lives

| What | Where |
|---|---|
| The page you look at | `web/` (HTML, script, flags) |
| The numbers the page reads | `web/data/*.json` — one player file and one match-log file per season, plus `manifest.json` |
| Original downloads from FPL / history | `raw/` — keep these; they are not rebuilt from the page |
| Temporary rebuilt tables | `marts/` — can be deleted; they are regenerated from `raw/` |

GitHub keeps `raw/` and `web/data/`. The large parquet files in `marts/` are not stored on GitHub.
