"""Idempotent update: snapshot bootstrap, fixtures, and any finalised live GWs."""

from __future__ import annotations

import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ingest.client import FplClient, finalised_gameweeks, season_from_bootstrap
from ingest.feed_fetch import latest_feed_fetch, utc_iso, write_feed_fetch
from ingest.plstats import refresh_current_season


def main() -> None:
    prev = (latest_feed_fetch() or {}).get("feeds") or {}
    feeds: dict[str, dict[str, str]] = {
        name: dict(row)
        for name, row in prev.items()
        if name != "style" and isinstance(row, dict) and row.get("as_of")
    }
    with FplClient() as client:
        print("=== bootstrap snapshot ===")
        bootstrap = client.snapshot_bootstrap()
        feeds["fpl"] = {"as_of": utc_iso(), "note": "bootstrap snapshot, fixtures"}
        season = season_from_bootstrap(bootstrap)
        print(f"current season {season}")
        print("=== event-status ===")
        event_status = client.snapshot_event_status()
        print("=== vaastav current season refresh ===")
        if client.download_vaastav(current_season=season):
            feeds["vaastav"] = {
                "as_of": utc_iso(),
                "note": "merged_gw, players_raw, fixtures",
            }
        else:
            print("vaastav live season fetch failed; keeping previous as_of", flush=True)
        print("=== fixtures ===")
        client.snapshot_fixtures(bootstrap)
        gws = finalised_gameweeks(bootstrap, event_status)
        print(f"finalised gameweeks: {gws}")
        for gw in gws:
            client.pull_live_if_missing(season, gw)
        if os.environ.get("FPL_PLSTATS") == "1":
            print("=== Pulse Opta current-season refresh ===")
            refresh_current_season(season)
            feeds["opta"] = {"as_of": utc_iso(), "note": "season totals"}
        else:
            print("Pulse Opta skipped (FPL_PLSTATS!=1)", flush=True)

    path = write_feed_fetch(feeds)
    print(f"wrote {path}  feeds={', '.join(sorted(feeds))}", flush=True)


if __name__ == "__main__":
    main()
