export PYTHONPATH := .

.PHONY: marts update backfill spot-check

marts:
	uv run python -m transform.build

update:
	uv run python ingest/update.py

backfill:
	uv run python ingest/backfill.py

spot-check:
	uv run python transform/spot_check.py
