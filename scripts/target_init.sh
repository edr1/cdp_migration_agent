#!/usr/bin/env bash
# Crea los esquemas destino en Trino (catalogo iceberg).
set -euo pipefail
cd "$(dirname "$0")/.."

echo -n "Esperando a Trino"
for _ in $(seq 1 60); do
  if docker compose --env-file .env -f docker/trino/docker-compose.yml exec -T trino \
       trino --execute "SELECT 1" >/dev/null 2>&1; then echo " listo"; break; fi
  echo -n "."; sleep 5
done

docker compose --env-file .env -f docker/trino/docker-compose.yml exec -T trino \
  trino --file /opt/cdp/sql/target/01_create_schemas.sql
docker compose --env-file .env -f docker/trino/docker-compose.yml exec -T trino \
  trino --execute "SHOW SCHEMAS FROM iceberg"
