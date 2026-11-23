-- =====================================================================
-- ORIGEN (Cloudera) - Bases de datos
-- Ejecutar con beeline contra HiveServer2.
-- =====================================================================
CREATE DATABASE IF NOT EXISTS db_production
  COMMENT 'Tablas de certificacion de equipos (Hive)';

CREATE DATABASE IF NOT EXISTS db_consulting
  COMMENT 'Tablas de consulta de equipos cloud (Impala)';
