export PYTHONPATH := .

.PHONY: marts web update backfill spot-check

marts:
	uv run python -m transform.build
	uv run python -m transform.export_web

web:
	uv run python -m transform.export_web

update:
	uv run python ingest/update.py

backfill:
	uv run python ingest/backfill.py

spot-check:
	uv run python transform/spot_check.py
