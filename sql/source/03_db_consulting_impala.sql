-- =====================================================================
-- ORIGEN (Cloudera) - db_consulting  (tablas Impala)
--
-- En CDP, Hive e Impala comparten el mismo Hive Metastore, por lo que estas
-- tablas Parquet se crean via HiveServer2 y quedan visibles en Impala
-- (ejecutar INVALIDATE METADATA en impala-shell si se usa el perfil impala).
--
-- NOTA: en la definicion original "db_consulting.vw_equipos_cloud_certificacion"
-- aparecia DOS veces con columnas distintas. Se conserva la version resumida
-- (17 columnas) con ese nombre y la version detallada (40 columnas) se nombra
-- "tbl_equipos_cloud_certificacion", siguiendo la convencion tbl_/vw_ de
-- db_production (TBL_CERTIFICACION_LOGS -> vw_certificacion_logs).
-- =====================================================================

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
