-- Consultas de reporte (consumidores BI)
SELECT negocio, mes_gs_registro,
       COUNT(*) AS logs,
       SUM(CASE WHEN estado_windows = 'CERTIFICADO' THEN 1 ELSE 0 END) AS win_certificados,
       collect_set(nm_servidor) AS servidores,
       percentile_approx(CAST(id_proceso AS INT), 0.5) AS p50_proceso
FROM db_production.tbl_certificacion_logs
WHERE fch_gs_registro BETWEEN '2026-01-01' AND '2026-10-31'
GROUP BY negocio, mes_gs_registro
ORDER BY negocio, mes_gs_registro;

SELECT nm_equipo, date_format(fch_peticion, 'yyyy-MM-dd') AS dia,
       nvl(usr_sensado_anterior, 'SIN_USUARIO') AS usr_anterior,
       regexp_extract(correo_electronico, '^([^@]+)@', 1) AS alias,
       from_unixtime(unix_timestamp(fch_peticion), 'yyyy-MM') AS mes,
       size(split(nm_ceco, ' ')) AS palabras_ceco
FROM db_consulting.tbl_equipos_cloud_certificacion
WHERE sumatoria < 6 AND tenant_mdr RLIKE '^MDR-(LATAM|MX)';

SELECT h.nm_computadora, s
FROM db_production.tbl_certificacion_hist h
LATERAL VIEW explode(h.sensores) t AS s
WHERE h.anio = 2026;

WITH ultimos AS (
  SELECT nm_equipo, MAX(fch_peticion) AS ult
  FROM db_consulting.tbl_equipos_cloud_certificacion
  GROUP BY nm_equipo
)
SELECT STRAIGHT_JOIN u.nm_equipo, u.ult, v.estado_de_certificacion
FROM ultimos u JOIN [BROADCAST] db_consulting.vw_equipos_cloud_certificacion v
  ON v.nm_equipo = u.nm_equipo AND v.fch_peticion = u.ult;

SELECT reflect('java.util.UUID', 'randomUUID') AS id, get_json_object(audit_path, '$.x') AS x
FROM db_consulting.tbl_equipos_cloud_certificacion LIMIT 1;
