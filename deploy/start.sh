#!/usr/bin/env bash
# Container entrypoint: gRPC microservices + Envoy (gRPC-Web gateway).
set -euo pipefail

PORT="${PORT:-8080}"
sed "s/__PORT__/${PORT}/g" /app/gateway/envoy.yaml > /tmp/envoy.yaml

cd /app/backend
python -m bioguard_svc.platform &
envoy -c /tmp/envoy.yaml --log-level warn &

# If any process dies, stop the container so the platform restarts it.
wait -n
exit 1
