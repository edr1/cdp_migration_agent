-- =====================================================================
-- ORIGEN (Cloudera) - Carga del dataset sintetico
-- Los archivos Parquet los genera data_generator/generate.py en ./data/source
-- y se montan en HiveServer2 como /opt/cdp/data/source (solo lectura).
-- LOAD DATA LOCAL copia los archivos al warehouse (no los mueve).
-- =====================================================================

LOAD DATA LOCAL INPATH '/opt/cdp/data/source/db_production/cat_certificacion_valor_sensor'
  OVERWRITE INTO TABLE db_production.cat_certificacion_valor_sensor;

LOAD DATA LOCAL INPATH '/opt/cdp/data/source/db_production/tbl_certificacion_logs'
  OVERWRITE INTO TABLE db_production.tbl_certificacion_logs;

LOAD DATA LOCAL INPATH '/opt/cdp/data/source/db_production/vw_certificacion_logs'
  OVERWRITE INTO TABLE db_production.vw_certificacion_logs;

LOAD DATA LOCAL INPATH '/opt/cdp/data/source/db_consulting/tbl_sensores_equipos_cloud'
  OVERWRITE INTO TABLE db_consulting.tbl_sensores_equipos_cloud;

LOAD DATA LOCAL INPATH '/opt/cdp/data/source/db_consulting/tbl_equipos_cloud_certificacion'
  OVERWRITE INTO TABLE db_consulting.tbl_equipos_cloud_certificacion;

LOAD DATA LOCAL INPATH '/opt/cdp/data/source/db_consulting/vw_equipos_cloud_certificacion'
  OVERWRITE INTO TABLE db_consulting.vw_equipos_cloud_certificacion;
