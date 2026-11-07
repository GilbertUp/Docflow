#!/bin/sh
# Proves the real path: scheduler -> outbox -> dispatcher -> Redis -> Celery -> worker -> storage.
# Creating a tenant writes an audit event. The scheduler then enqueues that tenant's
# audit.checkpoint job, and the worker writes the checkpoint to object storage.
set -eu

compose="docker compose --env-file .env -f deploy/compose.yaml -f deploy/compose.ci.yaml"

tenant=$($compose exec -T api docuflow create-tenant "CI Tenant $(date +%s)" | tail -n 1)
echo "tenant: $tenant"

attempt=0
while [ "$attempt" -lt 24 ]; do
  result=$($compose exec -T api docuflow verify-audit "$tenant" | tail -n 1)
  case "$result" in
    *'"checkpoints": {"ok": true, "checked": 0'*) ;;
    *'"checkpoints": {"ok": true'*)
      echo "live job completed: $result"
      exit 0
      ;;
  esac
  attempt=$((attempt + 1))
  sleep 5
done

echo "checkpoint job did not complete: $result"
echo "--- jobs"
$compose exec -T postgres psql -U postgres -d docuflow -c   "SELECT type, status, attempts, left(last_error, 300) AS error FROM processing_job"
echo "--- outbox"
$compose exec -T postgres psql -U postgres -d docuflow -c   "SELECT topic, published_at IS NOT NULL AS published, count(*) FROM outbox GROUP BY 1, 2"
echo "--- services"
$compose ps --all
for service in scheduler dispatcher worker; do
  echo "--- $service log"
  $compose logs --no-log-prefix --tail=25 "$service"
done
exit 1
