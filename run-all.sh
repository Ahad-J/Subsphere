#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

ACTION="up"
USE_PROD=false

for arg in "$@"; do
  case "$arg" in
    up|down|logs|ps|config|restart)
      ACTION="$arg"
      ;;
    --prod)
      USE_PROD=true
      ;;
    -h|--help)
      cat <<'EOF'
Usage: ./run-all.sh [up|down|ps|logs|restart|config] [--prod]

Starts or manages all SubSphere 4.0 project containers:
  Core:          postgres, backend, worker, report-worker, frontend
  Dev Mail:      mailpit
  AI Gateway:    litellm
  Metering:      clickhouse, openmeter
  Big Data:      kafka, minio, spark-master, spark-worker
  Observability: prometheus, grafana

Options:
  --prod         Include infra/compose/compose.production.yaml (Postal + Caddy)
EOF
      exit 0
      ;;
    *)
      echo "Unknown argument: $arg (use --help for usage)" >&2
      exit 1
      ;;
  esac
done

# Create .env from .env.example if missing and replace placeholder secrets for local run
if [[ ! -f "$ROOT_DIR/.env" && -f "$ROOT_DIR/.env.example" ]]; then
  cp "$ROOT_DIR/.env.example" "$ROOT_DIR/.env"
  echo "Created .env from .env.example"
fi

if [[ -f "$ROOT_DIR/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "$ROOT_DIR/.env"
  set +a
fi

export POSTGRES_DB="${POSTGRES_DB:-subsphere}"
export POSTGRES_USER="${POSTGRES_USER:-subsphere}"
if [[ -z "${POSTGRES_PASSWORD:-}" || "${POSTGRES_PASSWORD}" == "replace-with-strong-random-password" ]]; then
  export POSTGRES_PASSWORD="subsphere_local_pg_pass"
fi
if [[ -z "${APP_SECRET:-}" || "${APP_SECRET}" == "replace-with-strong-random-application-secret" ]]; then
  export APP_SECRET="subsphere_local_app_secret_change_in_prod"
fi
export LITELLM_URL="${LITELLM_URL:-http://litellm:4000}"
if [[ "${SMTP_USERNAME:-}" == "your.email@gmail.com" || "${SMTP_PASSWORD:-}" == "your_16_char_app_password" ]]; then
  export SMTP_HOST="mailpit"
  export SMTP_PORT="1025"
  export SMTP_USERNAME=""
  export SMTP_PASSWORD=""
  export SMTP_FROM_EMAIL="relay@mask.subsphere.local"
fi
export OPENMETER_URL="${OPENMETER_URL:-http://openmeter:8888}"

COMPOSE_FILES=(
  -f "$ROOT_DIR/infra/compose/compose.yaml"
  -f "$ROOT_DIR/infra/compose/compose.dev.yaml"
  -f "$ROOT_DIR/infra/compose/compose.ai.yaml"
  -f "$ROOT_DIR/infra/compose/compose.metering.yaml"
  -f "$ROOT_DIR/infra/compose/compose.analytics.yaml"
  -f "$ROOT_DIR/infra/compose/compose.observability.yaml"
  -f "$ROOT_DIR/infra/compose/compose.telemetry.yaml"
)

if [[ "$USE_PROD" == true ]]; then
  COMPOSE_FILES+=(-f "$ROOT_DIR/infra/compose/compose.production.yaml")
fi

case "$ACTION" in
  config)
    docker compose "${COMPOSE_FILES[@]}" --profile telemetry config --quiet
    echo "All Compose configurations are valid."
    exit 0
    ;;
  down)
    docker compose "${COMPOSE_FILES[@]}" --profile telemetry down
    exit 0
    ;;
  ps)
    docker compose "${COMPOSE_FILES[@]}" --profile telemetry ps
    exit 0
    ;;
  logs)
    docker compose "${COMPOSE_FILES[@]}" --profile telemetry logs -f
    exit 0
    ;;
  restart)
    docker compose "${COMPOSE_FILES[@]}" --profile telemetry restart
    exit 0
    ;;
  up)
    if ! docker info >/dev/null 2>&1; then
      echo "Error: Docker daemon is not running or not accessible." >&2
      exit 1
    fi

    echo "Pulling external Docker images (postgres, mailpit, clickhouse, kafka, prometheus, grafana)..."
    docker compose "${COMPOSE_FILES[@]}" --profile telemetry pull --ignore-buildable

    echo "Building and starting all SubSphere v4.1 containers..."
    docker compose "${COMPOSE_FILES[@]}" --profile telemetry up -d --build
    docker compose "${COMPOSE_FILES[@]}" exec -T frontend nginx -s reload >/dev/null 2>&1 || true

    echo "Running database migrations..."
    docker compose "${COMPOSE_FILES[@]}" run --rm --no-deps backend python -m alembic upgrade head

    if ! docker compose "${COMPOSE_FILES[@]}" ps --status running backend | grep -q backend; then
      echo "Backend container is not running; recent backend logs:" >&2
      docker compose "${COMPOSE_FILES[@]}" logs --tail=50 backend >&2 || true
      exit 1
    fi

    echo ""
    echo "SubSphere v4.1 stack is running:"
    echo "  Frontend UI:       http://127.0.0.1:8080"
    echo "  Backend API:       http://127.0.0.1:8000"
    echo "  Mailpit (Email):   http://127.0.0.1:8025"
    echo "  OTel Collector:    http://127.0.0.1:4317"
    echo "  LiteLLM Gateway:   http://127.0.0.1:4000"
    echo "  OpenMeter API:     http://127.0.0.1:8888"
    echo "  MinIO Console:     http://127.0.0.1:9001"
    echo "  Spark Master UI:   http://127.0.0.1:8081"
    echo "  Prometheus:        http://127.0.0.1:9090"
    echo "  Grafana:           http://127.0.0.1:3001"
    echo ""
    echo "Use './run-all.sh logs' to follow logs or './run-all.sh down' to stop."
    ;;
esac
