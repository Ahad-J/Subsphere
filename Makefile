SHELL := /usr/bin/env bash
COMPOSE_CORE := docker compose -f infra/compose/compose.yaml

.PHONY: up-core up-mail-dev up-telemetry up-ai migrate test test-email test-gateway test-otel test-calendar test-reports smoke

up-core:
	$(COMPOSE_CORE) up -d --build

up-mail-dev:
	$(COMPOSE_CORE) -f infra/compose/compose.dev.yaml up -d --build

up-telemetry:
	$(COMPOSE_CORE) -f infra/compose/compose.telemetry.yaml --profile telemetry up -d --build

up-ai:
	$(COMPOSE_CORE) -f infra/compose/compose.ai.yaml --profile ai up -d --build

migrate:
	cd backend && python3 -m alembic upgrade head

test:
	cd backend && python3 -m pytest -v

test-email:
	cd backend && python3 -m pytest tests/integration/test_v41_complete_matrix.py -k "mail" -v

test-gateway:
	cd backend && python3 -m pytest tests/integration/test_v41_complete_matrix.py -k "llm or telemetry" -v

test-otel:
	cd backend && python3 -m pytest tests/integration/test_v41_complete_matrix.py -k "otel or telemetry" -v

test-calendar:
	cd backend && python3 -m pytest tests/integration/test_v41_complete_matrix.py -k "cal" -v

test-reports:
	cd backend && python3 -m pytest tests/integration/test_v41_complete_matrix.py -k "pdf" -v

smoke:
	./run-all.sh config
	cd backend && python3 -m pytest -v
	cd frontend && npm run build
