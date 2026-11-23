-- Conteo de registros por tabla (comparar con data/source/manifest.json)
SELECT 'db_production.cat_certificacion_valor_sensor' AS tabla, COUNT(*) AS registros FROM db_production.cat_certificacion_valor_sensor
UNION ALL SELECT 'db_production.tbl_certificacion_logs', COUNT(*) FROM db_production.tbl_certificacion_logs
UNION ALL SELECT 'db_production.vw_certificacion_logs', COUNT(*) FROM db_production.vw_certificacion_logs
UNION ALL SELECT 'db_consulting.tbl_sensores_equipos_cloud', COUNT(*) FROM db_consulting.tbl_sensores_equipos_cloud
UNION ALL SELECT 'db_consulting.tbl_equipos_cloud_certificacion', COUNT(*) FROM db_consulting.tbl_equipos_cloud_certificacion
UNION ALL SELECT 'db_consulting.vw_equipos_cloud_certificacion', COUNT(*) FROM db_consulting.vw_equipos_cloud_certificacion;

-- Muestra de integridad: estados de sensores en logs que no existen en el catalogo (debe ser 0)
SELECT COUNT(*) AS cves_sin_catalogo
FROM db_production.tbl_certificacion_logs l
LEFT JOIN db_production.cat_certificacion_valor_sensor c
  ON l.cve_estado_crowd_strike = c.cve_valor_sensor
WHERE l.cve_estado_crowd_strike IS NOT NULL AND c.cve_valor_sensor IS NULL;
