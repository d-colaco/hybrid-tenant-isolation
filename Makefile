PY := .venv/bin/python
ALEMBIC := .venv/bin/alembic

.PHONY: setup install up down reset migrate seed test lint sync-exceptions bench move diagrams screenshots evidence

install:                ## virtualenv with the lab and its tools
	@test -x $(PY) || python3 -m venv .venv
	$(PY) -m pip install -e ".[dev,evidence]"

setup:                  ## write .env with generated passwords
	@python3 scripts/make_env.py

up: setup               ## the two PostgreSQL servers, migrated
	docker compose up -d --wait
	$(ALEMBIC) -x db=shared upgrade head
	$(ALEMBIC) -x db=heavy upgrade head

down:
	docker compose down

reset:                  ## drop both databases (the volumes); next `make up` starts empty
	docker compose down -v

migrate:
	$(ALEMBIC) -x db=shared upgrade head
	$(ALEMBIC) -x db=heavy upgrade head

test:                   ## needs `make up`
	$(PY) -m pytest -q

lint:
	.venv/bin/ruff check .

sync-exceptions:        ## copy exceptions.yaml (hash-checked) into the database
	$(PY) -m dclabaccount.exceptions sync

bench:                  ## noisy neighbour: seed 3M lines for BigCo, three conditions, writes reports/noisy-neighbour.json
	$(PY) -m dclabaccount.bench

move:                   ## rehearse a tenant move on the seeded corpus (writes reports/tenant-move.json)
	$(PY) -m dclabaccount.move

diagrams:               ## render demo/diagrams/*.mmd to SVG (light and dark) and PNG (needs Node and Chromium)
	$(PY) scripts/render_diagrams.py

screenshots:            ## rebuild the evidence frames in demo/screenshots (needs `make up` and Playwright's Chromium)
	PYTHONPATH=src $(PY) scripts/screenshots.py

evidence: diagrams screenshots
