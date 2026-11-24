-- =====================================================================
-- DESTINO - Tablas del catalogo Iceberg JDBC (PostgreSQL)
--
-- Trino NO crea estas tablas: su catalogo JDBC requiere que ya existan.
-- Sin ellas falla con "Cannot check and eventually update SQL schema".
-- Esquema V1 de Apache Iceberg (incluye iceberg_type para soportar vistas).
-- Idempotente: se ejecuta en el primer arranque de iceberg-postgres
-- (docker-entrypoint-initdb.d) y en cada "make target-init".
-- =====================================================================
CREATE TABLE IF NOT EXISTS iceberg_namespace_properties (
  catalog_name   VARCHAR(255) NOT NULL,
  namespace      VARCHAR(255) NOT NULL,
  property_key   VARCHAR(255),
  property_value VARCHAR(1000),
  PRIMARY KEY (catalog_name, namespace, property_key)
);

CREATE TABLE IF NOT EXISTS iceberg_tables (
  catalog_name               VARCHAR(255) NOT NULL,
  table_namespace            VARCHAR(255) NOT NULL,
  table_name                 VARCHAR(255) NOT NULL,
  metadata_location          VARCHAR(1000),
  previous_metadata_location VARCHAR(1000),
  iceberg_type               VARCHAR(5),
  PRIMARY KEY (catalog_name, table_namespace, table_name)
);
