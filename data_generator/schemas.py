"""Esquemas Arrow de las tablas del ORIGEN (Cloudera), alineados 1:1 con sql/source/*.sql.

Mapeo de tipos Hive -> Arrow/Parquet:
    STRING    -> pa.string()
    DATE      -> pa.date32()
    TIMESTAMP -> pa.timestamp("us")  (se escribe como INT96 para compatibilidad Hive/Impala)
    INT       -> pa.int32()
    BIGINT    -> pa.int64()
"""
import pyarrow as pa

S, D, TS, I32, I64 = pa.string(), pa.date32(), pa.timestamp("us"), pa.int32(), pa.int64()


def _schema(cols):
    return pa.schema([pa.field(name, typ, nullable=True) for name, typ in cols])


CAT_CERTIFICACION_VALOR_SENSOR = _schema([
    ("cve_valor_sensor", S),
    ("valor_sensor", S),
    ("valor_sensor_descripcion", S),
])

TBL_CERTIFICACION_LOGS = _schema([
    ("fch_gs_registro", D),
    ("semana_gs_registro", S),
    ("mes_gs_registro", S),
    ("cve_empleado", S),
    ("nm_empleado", S),
    ("correo_electronico", S),
    ("usr_red", S),
    ("negocio", S),
    ("estatus_empleado", S),
    ("nm_posicion", S),
    ("nm_ceco", S),
    ("nm_jefe", S),
    ("num_log", S),
    ("prioridad", S),
    ("version", S),
    ("fch_recepcion", TS),
    ("nm_servidor", S),
    ("recurso", S),
    ("id_proceso", S),
    ("nm_computadora", S),
    ("nm_dominio_computadora", S),
    ("sistema_operativo", S),
    ("version_sistema_operativo", S),
    ("nm_usr", S),
    ("nm_ultimo_usr_ini_sesion", S),
    ("direccion_ipv4", S),
    ("cve_estado_crowd_strike", S),
    ("cve_estado_sep_antivirus", S),
    ("cve_estado_carbon_black", S),
    ("cve_estado_symantec_dlp_wdp", S),
    ("cve_estado_symantec_dlp_edpa", S),
    ("cve_estado_jamf_pro_mac", S),
    ("cve_estado_tanium", S),
    ("cve_estado_symantec_dlp_seha", S),
    ("estado_windows", S),
    ("estado_mac", S),
    ("etiquetas_personalizadas_descr", S),
    ("indicador_recertificacion", S),
    ("recertificacion_descripcion", S),
    ("tiempo", S),
    ("consola", S),
    ("audit_fch_carga", TS),
])

VW_CERTIFICACION_LOGS = _schema([
    ("fch_gs_registro", D),
    ("fch_recepcion", TS),
    ("nm_computadora", S),
    ("sistema_operativo", S),
    ("estado_windows", S),
    ("estado_mac", S),
    ("estado_certificacion", S),
    ("valor_crowd_strike", S),
    ("estado_crowd_strike", S),
    ("valor_sep_antivirus", S),
    ("estado_sep_antivirus", S),
    ("valor_carbon_black", S),
    ("estado_carbon_black", S),
    ("valor_symantec_dlp_wdp", S),
    ("estado_symantec_dlp_wdp", S),
    ("valor_symantec_dlp_seha", S),
    ("estado_symantec_dlp_seha", S),
    ("valor_symantec_dlp_edpa", S),
    ("estado_symantec_dlp_edpa", S),
    ("valor_jamf_pro_mac", S),
    ("estado_jamf_pro_mac", S),
    ("valor_tanium", S),
    ("estado_tanium", S),
])

TBL_SENSORES_EQUIPOS_CLOUD = _schema([
    ("sensor_key", S),
    ("sensor_value", S),
    ("sensor_status", S),
])

VW_EQUIPOS_CLOUD_CERTIFICACION = _schema([
    ("fch_peticion", TS),
    ("nm_equipo", S),
    ("os_tipo", S),
    ("os_tipo_norm", S),
    ("estado_de_certificacion", S),
    ("crowstrike", S),
    ("crowstrike_estado", S),
    ("sep_antivirus", S),
    ("sep_estado", S),
    ("carbonblack", S),
    ("carbonblack_estado", S),
    ("symantec_dlp", S),
    ("symantec_dlp_estado", S),
    ("jamf_pro", S),
    ("jamf_estado", S),
    ("tanium", S),
    ("tanium_estado", S),
])

TBL_EQUIPOS_CLOUD_CERTIFICACION = _schema([
    ("fch_peticion", TS),
    ("nm_equipo", S),
    ("dominio_equipo", S),
    ("os_version", S),
    ("os_tipo", S),
    ("usr_sensado_actual", S),
    ("usr_sensado_anterior", S),
    ("ip_address", S),
    ("tenant_mdr", S),
    ("contador", I32),
    ("age", I32),
    ("crowstrike", S),
    ("crowstrike_estado", S),
    ("sep_antivirus", S),
    ("sep_estado", S),
    ("carbonblack", S),
    ("carbonblack_estado", S),
    ("symantec_dlp", S),
    ("symantec_dlp_estado", S),
    ("jamf_pro", S),
    ("jamf_estado", S),
    ("tanium", S),
    ("tanium_estado", S),
    ("sumatoria", I64),
    ("estado_de_certificacion", S),
    ("cve_empleado", I32),
    ("dominio", S),
    ("nm_empleado", S),
    ("correo_electronico", S),
    ("estatus_empleado", S),
    ("cve_instancia", S),
    ("id_instancia", S),
    ("nm_ceco", S),
    ("nm_funcion", S),
    ("nm_jefe", S),
    ("nm_posicion", S),
    ("nm_departamento", S),
    ("audit_path", S),
    ("audit_usr", S),
    ("audit_fch_carga", TS),
])

# (base de datos, tabla) -> esquema
TABLES = {
    ("db_production", "cat_certificacion_valor_sensor"): CAT_CERTIFICACION_VALOR_SENSOR,
    ("db_production", "tbl_certificacion_logs"): TBL_CERTIFICACION_LOGS,
    ("db_production", "vw_certificacion_logs"): VW_CERTIFICACION_LOGS,
    ("db_consulting", "tbl_sensores_equipos_cloud"): TBL_SENSORES_EQUIPOS_CLOUD,
    ("db_consulting", "tbl_equipos_cloud_certificacion"): TBL_EQUIPOS_CLOUD_CERTIFICACION,
    ("db_consulting", "vw_equipos_cloud_certificacion"): VW_EQUIPOS_CLOUD_CERTIFICACION,
}
