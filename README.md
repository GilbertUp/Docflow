# DocuFlow

Self-hosted document management and process automation platform.

**Status:** M0 foundation complete. There are no user-facing features yet. See [the feature matrix](docs/FEATURE_MATRIX.md) and [the implementation plan](docs/00-implementation-plan.md).

## Run the tests

Needs Python 3.12. No Docker is required: tests use an embedded PostgreSQL and a local S3 server.

```bash
py -3.12 -m venv .venv
```

```bash
.venv/Scripts/python -m pip install -e "backend[dev,embedded-pg]"
```

```bash
cd backend && ../.venv/Scripts/python -m pytest
```

## Run the stack

Needs Docker. Copy `.env.example` to `.env`, fill in every value, then:

```bash
docker compose --env-file .env -f deploy/compose.yaml up --build
```

Check it with `http://127.0.0.1:8000/health/ready`. API documentation is at `http://127.0.0.1:8000/api/v1/docs`.

Create an organization:

```bash
docker compose --env-file .env -f deploy/compose.yaml exec api docuflow create-tenant "RD Tech"
```

Migrations run automatically through the `migrate` service.
