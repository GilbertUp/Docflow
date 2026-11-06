#!/bin/sh
# Prints a throwaway .env with fresh random values for CI runs.
set -eu

secret() { openssl rand -hex 24; }
key() { openssl rand -base64 32; }

echo "DOCUFLOW_ENVIRONMENT=development"
for name in POSTGRES_PASSWORD DOCUFLOW_MIGRATOR_PASSWORD DOCUFLOW_APP_PASSWORD \
  DOCUFLOW_PLATFORM_PASSWORD DOCUFLOW_S3_ACCESS_KEY DOCUFLOW_S3_SECRET_KEY; do
  echo "$name=$(secret)"
done
echo "DOCUFLOW_MASTER_KEY=$(key)"
echo "DOCUFLOW_AUDIT_HMAC_KEY=$(key)"
