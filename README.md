# DocuFlow

Self-hosted document management and process automation platform.

**Status:** M0 foundation complete. M1 (sign-in, permissions, cabinets and folders) is built and under review: it has a web client, but upload and document processing arrive in M2. See [the feature matrix](docs/FEATURE_MATRIX.md) and [the implementation plan](docs/00-implementation-plan.md).

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

## Try it without Docker

A local demo with an embedded database. It is for looking around, not for production.

```bash
.venv/Scripts/python backend/scripts/dev_server.py --seed
```

This starts the API on port 8000 and creates a demo organization with Finance and HR cabinets. The generated sign-in details are written to `.data/dev/credentials.txt`. In a second terminal:

```bash
cd frontend && npm install && npm run dev
```

Then open `http://127.0.0.1:5173` and sign in with organization `demo`. Felix sees only Finance and Hana only HR, which shows the permission model working.

Web client checks: `npm test`, `npm run typecheck` and `npm run build` in the `frontend` folder. After changing the API, run `npm run gen:api` to refresh the generated types, which CI checks.

## Run the stack

Needs Docker. Copy `.env.example` to `.env`, fill in every value, then:

```bash
docker compose --env-file .env -f deploy/compose.yaml up --build
```

The web client is at `http://127.0.0.1:8080`. Check the API with `http://127.0.0.1:8000/health/ready`; its documentation is at `http://127.0.0.1:8000/api/v1/docs`.

Create an organization:

```bash
docker compose --env-file .env -f deploy/compose.yaml exec api docuflow create-tenant "RD Tech"
```

Migrations run automatically through the `migrate` service.
