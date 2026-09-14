export PYTHONPATH := .

.PHONY: marts web update backfill spot-check plstats

marts:
	uv run python -m transform.build
	uv run python -m transform.export_web

web:
	uv run python -m transform.export_web

update:
	uv run python ingest/update.py

backfill:
	uv run python ingest/backfill.py

plstats:
	uv run python -m ingest.plstats

spot-check:
	uv run python transform/spot_check.py
