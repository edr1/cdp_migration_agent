CREATE TABLE IF NOT EXISTS db_consulting.tbl_sensores_equipos_cloud (
    sensor_key STRING,
    sensor_value STRING,
    sensor_status STRING
)
STORED AS PARQUET;

CREATE TABLE IF NOT EXISTS db_consulting.vw_equipos_cloud_certificacion (
    fch_peticion TIMESTAMP,
    nm_equipo STRING,
    os_tipo STRING,
    os_tipo_norm STRING,
    estado_de_certificacion STRING,
    crowstrike STRING,
    crowstrike_estado STRING,
    sep_antivirus STRING,
    sep_estado STRING,
    carbonblack STRING,
    carbonblack_estado STRING,
    symantec_dlp STRING,
    symantec_dlp_estado STRING,
    jamf_pro STRING,
    jamf_estado STRING,
    tanium STRING,
    tanium_estado STRING
)
STORED AS PARQUET;

CREATE TABLE IF NOT EXISTS db_consulting.tbl_equipos_cloud_certificacion (
    fch_peticion TIMESTAMP,
    nm_equipo STRING,
    dominio_equipo STRING,
    os_version STRING,
    os_tipo STRING,
    usr_sensado_actual STRING,
    usr_sensado_anterior STRING,
    ip_address STRING,
    tenant_mdr STRING,
    contador INT,
    age INT,
    crowstrike STRING,
    crowstrike_estado STRING,
    sep_antivirus STRING,
    sep_estado STRING,
    carbonblack STRING,
    carbonblack_estado STRING,
    symantec_dlp STRING,
    symantec_dlp_estado STRING,
    jamf_pro STRING,
    jamf_estado STRING,
    tanium STRING,
    tanium_estado STRING,
    sumatoria BIGINT,
    estado_de_certificacion STRING,
    cve_empleado INT,
    dominio STRING,
    nm_empleado STRING,
    correo_electronico STRING,
    estatus_empleado STRING,
    cve_instancia STRING,
    id_instancia STRING,
    nm_ceco STRING,
    nm_funcion STRING,
    nm_jefe STRING,
    nm_posicion STRING,
    nm_departamento STRING,
    audit_path STRING,
    audit_usr STRING,
    audit_fch_carga TIMESTAMP
)
STORED AS PARQUET;

-- Impala: tabla con SORT BY y particion
CREATE TABLE IF NOT EXISTS db_consulting.tbl_resumen_diario (
  fch DATE,
  tenant_mdr STRING,
  total_equipos BIGINT,
  certificados BIGINT
)
PARTITIONED BY (anio INT)
SORT BY (fch, tenant_mdr)
STORED AS PARQUET;

ALTER TABLE db_consulting.tbl_equipos_cloud_certificacion ADD COLUMNS (region STRING COMMENT 'Region cloud', costo DECIMAL(12,2));
ALTER TABLE db_consulting.tbl_equipos_cloud_certificacion CHANGE COLUMN contador contador BIGINT;
