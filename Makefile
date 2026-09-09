.PHONY: up down logs test test-integration test-chaos lint typecheck load

up:
	docker compose up --build -d

up-observe:
	docker compose --profile observability up --build -d

down:
	docker compose --profile observability down -v

logs:
	docker compose logs -f api worker-default worker-browser beat

test:
	pytest

test-integration:
	pytest -m integration

test-chaos:
	pytest -m chaos

lint:
	ruff check . && ruff format --check .

typecheck:
	mypy scrapequeue

load:
	k6 run loadtest/jobs.js
