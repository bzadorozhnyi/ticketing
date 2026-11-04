#!/usr/bin/env bash
# Run from the repository root with the stack up and seeded:
#   docker compose up -d --wait
#   docker compose run --rm catalog python -m scripts.seed
#   experiments/m1-liveness-readiness/run.sh | tee experiments/m1-liveness-readiness/results/latest.txt
set -u

BASE=http://localhost:8001

code() { curl -s -o /dev/null -m 10 -w '%{http_code}' "$BASE$1"; }
snapshot() { echo "$1: /health=$(code /health) /ready=$(code /ready) /api/v1/events=$(code /api/v1/events)"; }
started_at() { docker inspect -f '{{.State.StartedAt}}' "$(docker compose ps -q catalog)"; }
warm_pool() { for _ in 1 2 3 4 5; do code /api/v1/events >/dev/null & done; wait; }

before=$(started_at)

echo "== 1. baseline"
snapshot "db up"

echo "== 2. db stopped"
docker compose stop catalog-db >/dev/null 2>&1
for i in 1 2 3; do
  snapshot "outage check $i"
done
echo "error body: $(curl -s -m 10 "$BASE/api/v1/events")"
echo "error content-type: $(curl -s -o /dev/null -m 10 -w '%{content_type}' "$BASE/api/v1/events")"

echo "== 3. db started, no catalog restart"
start=$(python3 -c 'import time; print(time.time())')
docker compose start catalog-db >/dev/null 2>&1
until [ "$(code /ready)" = "200" ]; do sleep 0.1; done
python3 -c "import time; print(f'ready again after {time.time() - $start:.2f}s')"
snapshot "recovered"

echo "== 4. quick db restart, warm pool, first requests after it"
warm_pool
docker compose restart catalog-db >/dev/null 2>&1
until docker compose exec -T catalog-db pg_isready -q -U postgres -d catalog_db; do sleep 0.1; done
codes=""
for _ in 1 2 3 4 5 6 7 8 9 10; do codes="$codes $(code /api/v1/events)"; done
echo "first 10 requests:$codes"

echo "== 5. catalog process was never restarted"
[ "$before" = "$(started_at)" ] && echo "same StartedAt: $before" || echo "RESTARTED"
