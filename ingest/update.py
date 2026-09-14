"""Idempotent update: snapshot bootstrap, fixtures, and any finalised live GWs."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ingest.client import FplClient, finalised_gameweeks, season_from_bootstrap


def main() -> None:
    with FplClient() as client:
        print("=== bootstrap snapshot ===")
        bootstrap = client.snapshot_bootstrap()
        season = season_from_bootstrap(bootstrap)
        print(f"current season {season}")
        print("=== event-status ===")
        event_status = client.snapshot_event_status()
        print("=== vaastav current season refresh ===")
        client.download_vaastav(current_season=season)
        print("=== fixtures ===")
        client.snapshot_fixtures(bootstrap)
        gws = finalised_gameweeks(bootstrap, event_status)
        print(f"finalised gameweeks: {gws}")
        for gw in gws:
            client.pull_live_if_missing(season, gw)


if __name__ == "__main__":
    main()
