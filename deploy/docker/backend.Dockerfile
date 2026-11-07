FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

RUN groupadd --system --gid 10001 docuflow \
 && useradd --system --uid 10001 --gid docuflow --no-create-home docuflow

WORKDIR /app
COPY backend/pyproject.toml backend/alembic.ini ./
COPY backend/src ./src
COPY backend/migrations ./migrations
RUN pip install . \
 && python -c "import kombu.transport.redis, docuflow.platform.jobs, docuflow.cli"

USER docuflow
EXPOSE 8000
CMD ["uvicorn", "docuflow.main:app_factory", "--factory", "--host", "0.0.0.0", "--port", "8000"]
