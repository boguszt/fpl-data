"""One-shot: vaastav seasons + element-summary history_past, then rebuild marts."""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ingest.client import FplClient, season_from_bootstrap
from transform.build import build_marts


def main() -> None:
    with FplClient() as client:
        print("=== bootstrap snapshot ===")
        bootstrap = client.snapshot_bootstrap()
        season = season_from_bootstrap(bootstrap)
        print(f"current season {season}")
        print("=== vaastav (past skip-if-present; current season dated refresh) ===")
        client.download_vaastav(current_season=season)
        print("=== vaastav understat ===")
        client.download_understat()
        print("=== fixtures snapshot ===")
        client.snapshot_fixtures(bootstrap)
        print("=== event-status snapshot ===")
        client.snapshot_event_status()
        print("=== element-summary history_past ===")
        client.cache_history_past(bootstrap)
    print("=== transform ===")
    build_marts()


if __name__ == "__main__":
    main()
