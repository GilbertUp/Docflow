FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1

RUN groupadd --system --gid 10001 docuflow \
 && useradd --system --uid 10001 --gid docuflow --no-create-home docuflow

# Text recognition (F04). Tesseract reads a language only if its pack is installed, so the
# packs are an image setting. Pass a space-separated list of Tesseract pack names, for example
#   --build-arg OCR_LANGUAGES="eng fra swa"
# Empty means the long default list below. A pack the distribution does not have is skipped
# and named in the build log, so one wrong name never breaks the build.
ARG OCR_LANGUAGES=""
RUN set -eu; \
    default="eng fra deu spa ita por nld rus ukr pol tur ara heb fas hin ben tam urd tha vie ind swa amh chi-sim chi-tra jpn kor ell ces dan fin nor swe hun ron bul hrv srp slk slv cat lav lit"; \
    langs="${OCR_LANGUAGES:-$default}"; \
    apt-get update; \
    packs=""; \
    for lang in $langs; do \
      if apt-cache show "tesseract-ocr-$lang" > /dev/null 2>&1; then \
        packs="$packs tesseract-ocr-$lang"; \
      else \
        echo "OCR language pack not available, skipped: $lang"; \
      fi; \
    done; \
    apt-get install --yes --no-install-recommends tesseract-ocr $packs; \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY backend/pyproject.toml backend/alembic.ini ./
COPY backend/src ./src
COPY backend/migrations ./migrations
RUN pip install . \
 && python -c "import kombu.transport.redis, docuflow.platform.jobs, docuflow.cli"

USER docuflow
EXPOSE 8000
CMD ["uvicorn", "docuflow.main:app_factory", "--factory", "--host", "0.0.0.0", "--port", "8000"]
