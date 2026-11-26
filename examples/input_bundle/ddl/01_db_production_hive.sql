#------ Hive (export de Cloudera CDP)
CREATE DATABASE IF NOT EXISTS db_production;
CREATE DATABASE IF NOT EXISTS db_consulting;

CREATE TABLE IF NOT EXISTS db_production.CAT_CERTIFICACION_VALOR_SENSOR (
  cve_valor_sensor STRING,
  valor_sensor STRING,
  valor_sensor_descripcion STRING
)
STORED AS PARQUET;

CREATE TABLE IF NOT EXISTS db_production.TBL_CERTIFICACION_LOGS (
  fch_gs_registro date,
  semana_gs_registro STRING,
  mes_gs_registro STRING,
  cve_empleado STRING,
  nm_empleado STRING,
  correo_electronico STRING,
  usr_red STRING,
  negocio STRING,
  estatus_empleado STRING,
  nm_posicion STRING,
  nm_ceco STRING,
  nm_jefe STRING,
  num_log STRING,
  prioridad STRING,
  version STRING,
  fch_recepcion TIMESTAMP,
  nm_servidor STRING,
  recurso STRING,
  id_proceso STRING,
  nm_computadora STRING,
  nm_dominio_computadora STRING,
  sistema_operativo STRING,
  version_sistema_operativo STRING,
  nm_usr STRING,
  nm_ultimo_usr_ini_sesion STRING,
  direccion_ipv4 STRING,
  cve_estado_crowd_strike STRING,
  cve_estado_sep_antivirus STRING,
  cve_estado_carbon_black STRING,
  cve_estado_symantec_dlp_wdp STRING,
  cve_estado_symantec_dlp_edpa STRING,
  cve_estado_jamf_pro_mac STRING,
  cve_estado_tanium STRING,
  cve_estado_symantec_dlp_seha STRING,
  estado_windows STRING,
  estado_mac STRING,
  etiquetas_personalizadas_descr STRING,
  indicador_recertificacion STRING,
  recertificacion_descripcion STRING,
  tiempo STRING,
  consola STRING,
  audit_fch_carga TIMESTAMP
)
STORED AS PARQUET;

CREATE TABLE IF NOT EXISTS db_production.vw_certificacion_logs (
  fch_gs_registro date,
  fch_recepcion TIMESTAMP,
  nm_computadora STRING,
  sistema_operativo STRING,
  estado_windows STRING,
  estado_mac STRING,
  estado_certificacion STRING,
  valor_crowd_strike STRING,
  estado_crowd_strike STRING,
  valor_sep_antivirus STRING,
  estado_sep_antivirus STRING,
  valor_carbon_black STRING,
  estado_carbon_black STRING,
  valor_symantec_dlp_wdp STRING,
  estado_symantec_dlp_wdp STRING,
  valor_symantec_dlp_seha STRING,
  estado_symantec_dlp_seha STRING,
  valor_symantec_dlp_edpa STRING,
  estado_symantec_dlp_edpa STRING,
  valor_jamf_pro_mac STRING,
  estado_jamf_pro_mac STRING,
  valor_tanium STRING,
  estado_tanium STRING
)
STORED AS PARQUET;

-- Tabla particionada y con buckets (caso tipico CDP)
CREATE EXTERNAL TABLE IF NOT EXISTS db_production.tbl_certificacion_hist (
  nm_computadora STRING COMMENT 'Nombre del equipo',
  estado_certificacion STRING,
  score DECIMAL(5,2),
  sensores ARRAY<STRING>,
  atributos MAP<STRING,STRING>,
  ubicacion STRUCT<sitio:STRING, piso:INT>,
  cve_tipo CHAR(2),
  descripcion VARCHAR(200),
  fch_evento TIMESTAMP
)
PARTITIONED BY (anio INT, mes STRING)
CLUSTERED BY (nm_computadora) SORTED BY (fch_evento) INTO 16 BUCKETS
STORED AS PARQUET
LOCATION 'hdfs://nameservice1/warehouse/db_production/tbl_certificacion_hist'
TBLPROPERTIES ('parquet.compression'='SNAPPY', 'external.table.purge'='true');

CREATE VIEW IF NOT EXISTS db_production.v_equipos_no_certificados AS
SELECT nm_computadora, sistema_operativo, NVL(estado_windows, estado_mac) AS estado, fch_recepcion
FROM db_production.vw_certificacion_logs
WHERE estado_certificacion = 'NO CERTIFICADO';
