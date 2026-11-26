#!/usr/bin/env bash
# Crea bases/tablas en el ORIGEN (Hive) y carga el dataset sintetico.
# Requiere: stack origen arriba (make source-up) y dataset generado (make data).
set -euo pipefail
cd "$(dirname "$0")/.."

COMPOSE="docker compose --env-file .env -f docker/cloudera/docker-compose.yml"
BEELINE="/opt/hive/bin/beeline -u jdbc:hive2://localhost:10000/ -n hive --silent=true --showHeader=true --outputformat=table"

if [ ! -f data/source/manifest.json ]; then
  echo "No existe data/source/manifest.json. Ejecuta primero: make data" >&2
  exit 1
fi

for f in 01_create_databases.sql 02_db_production_hive.sql 03_db_consulting_impala.sql 10_load_synthetic_data.sql 20_validate_counts.sql; do
  echo ">>> Ejecutando sql/source/$f"
  $COMPOSE exec -T hiveserver2 $BEELINE -f "/opt/cdp/sql/source/$f"
done

# Si el perfil Impala esta activo, refrescar su catalogo
if $COMPOSE ps --status running --services 2>/dev/null | grep -q '^impalad$'; then
  echo ">>> Refrescando metadatos en Impala"
  $COMPOSE exec -T impalad impala-shell -i localhost:21050 -q "INVALIDATE METADATA" || true
fi
echo "Carga del origen completada."
