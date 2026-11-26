-- ETL diario Hive (db_production)
SET hive.exec.dynamic.partition=true;
SET hive.exec.dynamic.partition.mode=nonstrict;
ADD JAR hdfs:///libs/udf-certificacion.jar;
CREATE TEMPORARY FUNCTION normaliza_os AS 'com.empresa.udf.NormalizaOS';
USE db_production;

LOAD DATA INPATH '/data/landing/certificacion/2026-11-01' INTO TABLE db_production.tbl_certificacion_logs;

INSERT OVERWRITE TABLE db_production.vw_certificacion_logs
SELECT l.fch_gs_registro, l.fch_recepcion, l.nm_computadora, l.sistema_operativo,
       l.estado_windows, l.estado_mac,
       CASE WHEN l.estado_windows = 'CERTIFICADO' OR l.estado_mac = 'CERTIFICADO' THEN 'CERTIFICADO' ELSE 'NO CERTIFICADO' END,
       cs.valor_sensor,   IF(l.cve_estado_crowd_strike IN ('1','4'), 'CUMPLE', 'NO CUMPLE'),
       sep.valor_sensor,  IF(l.cve_estado_sep_antivirus IN ('1','4'), 'CUMPLE', 'NO CUMPLE'),
       cb.valor_sensor,   IF(l.cve_estado_carbon_black IN ('1','4'), 'CUMPLE', 'NO CUMPLE'),
       wdp.valor_sensor,  IF(l.cve_estado_symantec_dlp_wdp IN ('1','4'), 'CUMPLE', 'NO CUMPLE'),
       seha.valor_sensor, IF(l.cve_estado_symantec_dlp_seha IN ('1','4'), 'CUMPLE', 'NO CUMPLE'),
       edpa.valor_sensor, IF(l.cve_estado_symantec_dlp_edpa IN ('1','4'), 'CUMPLE', 'NO CUMPLE'),
       jamf.valor_sensor, IF(l.cve_estado_jamf_pro_mac IN ('1','4'), 'CUMPLE', 'NO CUMPLE'),
       tan.valor_sensor,  IF(l.cve_estado_tanium IN ('1','4'), 'CUMPLE', 'NO CUMPLE')
FROM tbl_certificacion_logs l
LEFT JOIN cat_certificacion_valor_sensor cs   ON l.cve_estado_crowd_strike = cs.cve_valor_sensor
LEFT JOIN cat_certificacion_valor_sensor sep  ON l.cve_estado_sep_antivirus = sep.cve_valor_sensor
LEFT JOIN cat_certificacion_valor_sensor cb   ON l.cve_estado_carbon_black = cb.cve_valor_sensor
LEFT JOIN cat_certificacion_valor_sensor wdp  ON l.cve_estado_symantec_dlp_wdp = wdp.cve_valor_sensor
LEFT JOIN cat_certificacion_valor_sensor seha ON l.cve_estado_symantec_dlp_seha = seha.cve_valor_sensor
LEFT JOIN cat_certificacion_valor_sensor edpa ON l.cve_estado_symantec_dlp_edpa = edpa.cve_valor_sensor
LEFT JOIN cat_certificacion_valor_sensor jamf ON l.cve_estado_jamf_pro_mac = jamf.cve_valor_sensor
LEFT JOIN cat_certificacion_valor_sensor tan  ON l.cve_estado_tanium = tan.cve_valor_sensor
WHERE l.fch_gs_registro >= date_sub(current_date, 1);

INSERT OVERWRITE TABLE db_production.tbl_certificacion_hist PARTITION (anio=2026, mes='11')
SELECT nm_computadora, estado_certificacion, 100.0, array(valor_crowd_strike, valor_tanium),
       map('so', sistema_operativo), named_struct('sitio', 'CDMX', 'piso', 12), 'WS',
       concat_ws('|', estado_windows, estado_mac), fch_recepcion
FROM vw_certificacion_logs;

INSERT INTO TABLE db_production.tbl_certificacion_logs
SELECT * FROM db_production.tbl_certificacion_logs WHERE fch_gs_registro = '2026-10-31';

TRUNCATE TABLE db_production.cat_certificacion_valor_sensor;
INSERT INTO db_production.cat_certificacion_valor_sensor VALUES ('0','NO INSTALADO','El sensor no esta instalado'), ('1','INSTALADO','Sensor instalado');

FROM db_production.tbl_certificacion_logs l
INSERT INTO TABLE db_production.tbl_certificacion_hist PARTITION (anio, mes)
SELECT l.nm_computadora, 'X', 0, NULL, NULL, NULL, NULL, NULL, l.fch_recepcion, 2026, '11';
