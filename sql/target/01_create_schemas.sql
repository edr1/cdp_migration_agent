-- =====================================================================
-- DESTINO (Trino / Iceberg) - Esquemas destino de la migracion.
-- Las tablas las creara el agente de migracion (mapeo de tipos Hive -> Trino).
-- =====================================================================
CREATE SCHEMA IF NOT EXISTS iceberg.db_production;
CREATE SCHEMA IF NOT EXISTS iceberg.db_consulting;
