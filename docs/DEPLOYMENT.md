# Modular Docker Compose architecture and deployment guide

## What is actually in the starter?

The checked-in starter implements a **minimal greenfield boot skeleton only**: React/Vite landing screen, FastAPI health/info endpoints, PostgreSQL service and optional Mailpit. Business modules, migrations, OpenMeter/LiteLLM/Postal integration, PDF worker, Kafka/Spark data jobs are **planned components**, not yet built. An implementation agent must complete milestones M0–M7 and add actual runtime Compose services as they are implemented. Never represent a placeholder as a working integration.

## Deployment layout — target

```text
infra/compose/
  compose.yaml                 # Core API + frontend + PostgreSQL (starter)
  compose.dev.yaml             # Mailpit and developer tooling (starter)
  compose.ai.yaml              # LiteLLM gateway (M5)
  compose.metering.yaml        # OpenMeter + workers, ClickHouse, dependencies (M6)
  compose.analytics.yaml       # Kafka + Spark + MinIO (M6)
  compose.observability.yaml   # Prometheus + Grafana (M7)
  compose.production.yaml      # Hardened ingress, replicas, override (M7)
infra/postal/
  README.md                    # Separate official Postal deployment stack (M4)
```

**Do not use `profiles` for the core API/database**: those must always work. For optional stacks use Compose `-f` files and/or profiles; pin image digests and exact Python/Node package locks before production. `docker compose config` must validate every documented combination. Never hand-maintain a partial copy of OpenMeter/Postal upstream Compose stack as if production-ready; vendor a reviewed, pinned release or use documented image services and their required runtime workers.

## Working starter commands (from repository root)

```bash
cp .env.example .env
# Change the POSTGRES_PASSWORD and APP_SECRET in .env
docker compose -f infra/compose/compose.yaml -f infra/compose/compose.dev.yaml config
docker compose -f infra/compose/compose.yaml -f infra/compose/compose.dev.yaml up -d --build
```

After startup, the intended starter addresses are `http://localhost:8080` (landing screen), `http://localhost:8000/health/live` and `/health/ready`, and Mailpit `http://localhost:8025`. These are configured starter endpoints, not proof of runtime validation: this environment does not have a Docker engine. Health readiness must report 503 if the database cannot be queried.

## Target module enablement after implementation

```bash
# AI gateway; requires pinned image + provider configuration
# docker compose -f infra/compose/compose.yaml -f infra/compose/compose.ai.yaml up -d

# Metering: use pinned compatible OpenMeter API and worker release, scoped databases
# docker compose -f infra/compose/compose.yaml -f infra/compose/compose.metering.yaml up -d

# Big Data: Kafka/Spark/MinIO, checkpoint/object persistence
# docker compose -f infra/compose/compose.yaml -f infra/compose/compose.analytics.yaml up -d

# Full local demonstration after all modules are implemented
# docker compose -f infra/compose/compose.yaml -f infra/compose/compose.dev.yaml \
#   -f infra/compose/compose.ai.yaml -f infra/compose/compose.metering.yaml \
#   -f infra/compose/compose.analytics.yaml up -d
```

Mailpit is development-only and not a general outgoing SMTP relay. Postal is a separate officially installed Compose deployment and connects to SubSphere via DNS/TLS/SMTP credentials.

## Component allocation and dependencies

| Module | Services | Critical storage | Depends on | Should survive failure of |
|---|---|---|---|---|
| Core | API, frontend, Postgres, scheduler | Postgres named volume | Postgres | Kafka, Spark, OpenMeter, LiteLLM, Postal |
| Reports | WeasyPrint worker, private file store | MinIO/private objects + report snapshots in PG | PG, object store | Postal, LiteLLM |
| AI | LiteLLM, its isolated database | Separate Postgres database | AI provider credentials for live requests | Spark, OpenMeter |
| Metering | OpenMeter API/worker, ClickHouse, suitable broker, optional Redis | ClickHouse + scoped PostgreSQL | Kafka depending on pinned runtime | Postal |
| Analytics | Kafka, Spark, MinIO | Kafka log, MinIO datasets/checkpoints | Kafka/object store | Frontend (jobs asynchronous) |
| Email development | Mailpit | Ephemeral mailbox dev only | None | Entire production platform |
| Production email | Postal independently | Postal's own state per official release | DNS, SMTP connectivity, sender verification | AI data plane |

## Cross-host production topology

**Host A:** reverse proxy, frontend, backend, worker, PostgreSQL, report worker.  
**Host B:** Kafka, Spark, MinIO, OpenMeter, ClickHouse, LiteLLM (size and isolate appropriately).  
**Host C:** independent Postal installation with correct hostname, DNS, valid TLS and outbound port availability.

These are logical groups, **not a mandate for three machines**. A startup could begin on one adequately provisioned host and migrate modules later. Define `KAFKA_BOOTSTRAP_SERVERS`, `MINIO_ENDPOINT`, `OPENMETER_URL`, `LITELLM_URL`, `SMTP_HOST` as actual reachable DNS/HTTPS addresses across hosts. Docker service names work only on the appropriate Docker network. Configure TLS/private networks and firewall allowlists. Never publish Postgres/ClickHouse/Kafka administration ports to the public internet.

## Operations, resilience and failure-mode behavior

- PostgreSQL operation is required for critical API writes; `503` and no committed mutation if unavailable.
- Kafka down: operational commits continue with pending transactional outbox; do not drop accepted usage events.
- Spark down: operational pages work; historical analytics display stale timestamp.
- OpenMeter down: durable retry queues; hard quota still enforced by PostgreSQL.
- LiteLLM down: explicitly configured gateway requests may fail; personal manual tracking still works.
- Postal down: email delivery jobs retry; billing changes remain committed.
- Report worker down: document job queued/failed; do not falsely mark report sent.
- Use `docker compose stop`, `restart`, `down` to test recovery; never use `down -v` for operational environments.

## Deploy/security checklist

1. Pin Docker image tags and ideally digests; pin Python/npm lockfiles; produce SBOM.
2. Create isolated database users and databases: `subsphere`, `litellm`, `openmeter` (separate DB credentials).
3. Use Docker secrets or locked-down secret storage for JWT, API, LiteLLM, SMTP and integration keys. Frontend never receives server credentials.
4. Use non-root app containers and resource/CPU/memory limits where supported.
5. Implement readiness/liveness probes and correct startup/migration ordering.
6. Add a true reverse-proxy/TLS setup, secure cookies, CORS and CSRF protection, consistent public URL.
7. Scheduled database backups, private MinIO object backups, ClickHouse/OpenMeter backups according to RPO/RTO, and verified restore drills.
8. Add migrations with forward/backward compatibility, isolate migration job, no resetting DB on startup.
9. Preserve Kafka topic policies and Spark checkpoint state; choose retention aligned to replay/backfill requirements.
10. Email sender domains must complete DNS identity verification; block spoofed/unverified From addresses.
11. Rate limit signup, login, test messages, exports, webhook attempts and metering endpoints.
12. Run on a mail-capable network if Postal is used; upstream recommends a dedicated server and adequate storage/RAM, and outbound port 25 may be blocked.
13. Review upstream project license and editions before enabling proprietary/enterprise-only capabilities.

## Upstream reference links

- Docker Compose profiles: https://docs.docker.com/compose/how-tos/profiles/
- Docker Compose includes: https://docs.docker.com/compose/how-tos/multiple-compose-files/include/
- Docker secrets: https://docs.docker.com/compose/how-tos/use-secrets/
- LiteLLM proxy deployment: https://docs.litellm.ai/docs/proxy/deploy
- OpenMeter architecture: https://openmeter.io/docs/open-source/architecture
- Postal prerequisites: https://docs.postalserver.io/getting-started/prerequisites/
- Postal helper commands: https://docs.postalserver.io/getting-started/postal-command/
