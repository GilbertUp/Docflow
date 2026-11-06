#!/bin/sh
# First-boot only. Runs bootstrap.sql, then gives the three roles their login passwords.
set -eu

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -f /docker-entrypoint-initdb.d/bootstrap.sql.inc

psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" \
  -v migrator="$DOCUFLOW_MIGRATOR_PASSWORD" \
  -v app="$DOCUFLOW_APP_PASSWORD" \
  -v platform="$DOCUFLOW_PLATFORM_PASSWORD" <<'SQL'
ALTER ROLE docuflow_migrator LOGIN PASSWORD :'migrator';
ALTER ROLE docuflow_app LOGIN PASSWORD :'app';
ALTER ROLE docuflow_platform LOGIN PASSWORD :'platform';
SQL
