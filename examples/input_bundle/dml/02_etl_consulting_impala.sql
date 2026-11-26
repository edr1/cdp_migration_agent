-- ETL Impala (db_consulting)
INVALIDATE METADATA db_consulting.tbl_equipos_cloud_certificacion;

INSERT OVERWRITE db_consulting.vw_equipos_cloud_certificacion
SELECT /* +SHUFFLE */ e.fch_peticion, e.nm_equipo, e.os_tipo,
       CASE WHEN lower(e.os_tipo) LIKE 'win%' THEN 'WINDOWS'
            WHEN lower(e.os_tipo) IN ('macos','mac','mac os x','darwin') THEN 'MAC' ELSE 'LINUX' END,
       e.estado_de_certificacion, e.crowstrike, e.crowstrike_estado, e.sep_antivirus, e.sep_estado,
       e.carbonblack, e.carbonblack_estado, e.symantec_dlp, e.symantec_dlp_estado, e.jamf_pro, e.jamf_estado,
       e.tanium, e.tanium_estado
FROM db_consulting.tbl_equipos_cloud_certificacion e
JOIN [SHUFFLE] db_consulting.tbl_sensores_equipos_cloud s
  ON s.sensor_key = 'crowstrike' AND s.sensor_value = e.crowstrike
WHERE e.fch_peticion >= now() - INTERVAL 7 DAYS;

UPDATE db_consulting.tbl_equipos_cloud_certificacion SET estatus_empleado = 'BAJA' WHERE cve_empleado = 100123;
DELETE FROM db_consulting.tbl_equipos_cloud_certificacion WHERE age > 90;
UPSERT INTO db_consulting.tbl_sensores_equipos_cloud VALUES ('tanium', 'Installed', 'OK');

COMPUTE STATS db_consulting.vw_equipos_cloud_certificacion;
