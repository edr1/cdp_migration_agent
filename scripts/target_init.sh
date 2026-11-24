#!/usr/bin/env bash
# Inicializa el DESTINO:
#   1) crea las tablas del catalogo Iceberg JDBC en PostgreSQL (Trino no las crea)
#   2) crea los esquemas destino en Trino (catalogo iceberg)
set -euo pipefail
cd "$(dirname "$0")/.."
TGT="docker compose --env-file .env -f docker/trino/docker-compose.yml"

echo ">>> Creando tablas del catalogo Iceberg JDBC en iceberg-postgres"
$TGT exec -T iceberg-postgres sh -c 'psql -q -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"' \
  < sql/target/00_iceberg_jdbc_catalog.sql

echo -n "Esperando a Trino"
for _ in $(seq 1 60); do
  if $TGT exec -T trino trino --execute "SELECT 1" >/dev/null 2>&1; then echo " listo"; break; fi
  echo -n "."; sleep 5
done

echo ">>> Creando esquemas destino"
$TGT exec -T trino trino --file /opt/cdp/sql/target/01_create_schemas.sql
$TGT exec -T trino trino --execute "SHOW SCHEMAS FROM iceberg"
