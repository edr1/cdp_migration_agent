"""Generador de dataset sintetico para el ORIGEN (Cloudera: db_production + db_consulting).

Genera un archivo Parquet por tabla en  <out>/<base>/<tabla>/part-00000.parquet
y un manifest.json con conteos y esquema (util para validar la migracion a Trino).

Los datos son coherentes entre tablas:
  * Los cve_estado_* de TBL_CERTIFICACION_LOGS existen en CAT_CERTIFICACION_VALOR_SENSOR.
  * vw_certificacion_logs se deriva 1:1 de TBL_CERTIFICACION_LOGS + catalogo.
  * Los valores/estados de sensores cloud existen en tbl_sensores_equipos_cloud.
  * vw_equipos_cloud_certificacion se deriva 1:1 de tbl_equipos_cloud_certificacion.
  * Empleados y equipos se comparten entre db_production y db_consulting.
Incluye casos borde para probar la migracion: acentos/enies, NULLs, microsegundos.

Uso:
    python data_generator/generate.py --out data/source --seed 42 --scale 1.0
"""
from __future__ import annotations

import argparse
import json
import random
import shutil
import unicodedata
import uuid
from datetime import date, datetime, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
from faker import Faker

import schemas

# ----------------------------------------------------------------------------
# Catalogos de negocio
# ----------------------------------------------------------------------------
NEGOCIOS = ["BANCA", "RETAIL", "SEGUROS", "AFORE", "CORPORATIVO", "TECNOLOGIA", "CREDITO"]
ESTATUS_EMPLEADO = (["ACTIVO"] * 90) + (["BAJA"] * 6) + (["LICENCIA"] * 4)
POSICIONES = [
    "ANALISTA DE SISTEMAS", "INGENIERO DE DATOS", "GERENTE DE TI", "EJECUTIVO DE CUENTA",
    "ESPECIALISTA EN SEGURIDAD", "COORDINADOR DE OPERACIONES", "DIRECTOR DE AREA",
    "DESARROLLADOR SR", "SOPORTE TECNICO", "CONSULTOR FUNCIONAL", "AUDITOR INTERNO",
]
FUNCIONES = ["OPERACION", "SOPORTE", "DESARROLLO", "SEGURIDAD", "NEGOCIO", "STAFF", "AUDITORIA"]
DEPARTAMENTOS = [
    "INFRAESTRUCTURA", "CIBERSEGURIDAD", "DATOS Y ANALITICA", "OPERACIONES", "FINANZAS",
    "RECURSOS HUMANOS", "COMERCIAL", "JURIDICO", "CANALES DIGITALES",
]
CECOS = [f"CC{n:04d} {d}" for n, d in zip(range(1101, 1101 + len(DEPARTAMENTOS)), DEPARTAMENTOS)]
DOMINIOS = ["corp.empresa-demo.local", "mx.empresa-demo.local", "lat.empresa-demo.local"]
EMAIL_DOMAIN = "empresa-demo.com.mx"
SERVIDORES = ["SRVCERT01", "SRVCERT02", "SRVCERT03", "SRVGSLOG01"]
RECURSOS = ["/api/v1/certificacion", "/api/v1/sensores", "/api/v2/inventario", "/batch/recertificacion"]
CONSOLAS = ["GS-CONSOLE", "SCCM", "JAMF", "TANIUM", "FALCON"]
PRIORIDADES = ["ALTA"] * 15 + ["MEDIA"] * 50 + ["BAJA"] * 35
ETIQUETAS = [
    "VIP", "EQUIPO CRITICO", "HOME OFFICE", "SUCURSAL", "PISO 12", "LAPTOP PRESTAMO",
    "EXCEPCION SEGURIDAD", "MIGRACION W11", "PILOTO EDR",
]
TENANTS_MDR = ["MDR-LATAM-01", "MDR-LATAM-02", "MDR-MX-CORE", "MDR-CLOUD-AWS", "MDR-CLOUD-AZURE"]

OS_VERSIONS = {
    "Windows": ["10.0.19045", "10.0.22621", "10.0.22631", "10.0.26100"],
    "macOS": ["13.6.7", "14.5", "14.7.1", "15.1"],
    "Linux": ["Ubuntu 22.04", "Ubuntu 24.04", "RHEL 8.9", "RHEL 9.4"],
}
OS_TIPO_CLOUD = {"Windows": ["Windows", "WINDOWS", "Win10", "Win11"],
                 "macOS": ["macOS", "MAC", "Mac OS X", "Darwin"],
                 "Linux": ["Linux", "LINUX", "ubuntu", "rhel"]}
OS_NORM = {"Windows": "WINDOWS", "macOS": "MAC", "Linux": "LINUX"}

# --- db_production: catalogo de valores de sensor --------------------------
CAT_VALOR_SENSOR = [
    ("0", "NO INSTALADO", "El sensor no se encuentra instalado en el equipo"),
    ("1", "INSTALADO", "Sensor instalado y reportando correctamente a la consola"),
    ("2", "INSTALADO SIN REPORTAR", "Sensor instalado sin reportar en las últimas 72 horas"),
    ("3", "DESACTUALIZADO", "Sensor instalado con versión fuera de política"),
    ("4", "NO APLICA", "El sensor no aplica para el sistema operativo del equipo"),
    ("5", "ERROR", "Error de comunicación entre el sensor y su consola"),
]
CAT_BY_CVE = {c: (v, d) for c, v, d in CAT_VALOR_SENSOR}
CVE_CUMPLE = {"1", "4"}  # INSTALADO o NO APLICA => cumple
CVE_WEIGHTS = [("1", 93), ("2", 3), ("3", 2), ("0", 1.5), ("5", 0.5)]

# sensor -> sistemas operativos donde aplica
SENSORES_PROD = {
    "crowd_strike": {"Windows", "macOS"},
    "sep_antivirus": {"Windows"},
    "carbon_black": {"Windows"},
    "symantec_dlp_wdp": {"Windows"},
    "symantec_dlp_edpa": {"Windows", "macOS"},
    "jamf_pro_mac": {"macOS"},
    "tanium": {"Windows", "macOS"},
    "symantec_dlp_seha": {"Windows"},
}

# --- db_consulting: catalogo de sensores cloud ------------------------------
SENSORES_CLOUD = {
    "crowstrike": {"Windows", "macOS", "Linux"},
    "sep_antivirus": {"Windows"},
    "carbonblack": {"Windows", "Linux"},
    "symantec_dlp": {"Windows", "macOS"},
    "jamf_pro": {"macOS"},
    "tanium": {"Windows", "macOS", "Linux"},
}
CLOUD_VALUES = [("Installed", "OK"), ("Not Installed", "KO"), ("Not Reporting", "KO"),
                ("Outdated", "KO"), ("N/A", "OK")]
CLOUD_STATUS = dict(CLOUD_VALUES)
CLOUD_WEIGHTS = [("Installed", 92), ("Not Reporting", 4), ("Outdated", 2.5), ("Not Installed", 1.5)]
# columna del valor -> columna del estado en las tablas cloud
CLOUD_COLS = {
    "crowstrike": "crowstrike_estado",
    "sep_antivirus": "sep_estado",
    "carbonblack": "carbonblack_estado",
    "symantec_dlp": "symantec_dlp_estado",
    "jamf_pro": "jamf_estado",
    "tanium": "tanium_estado",
}


# ----------------------------------------------------------------------------
# Utilidades
# ----------------------------------------------------------------------------
def ascii_slug(text: str) -> str:
    return unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode().lower()


def weighted(rng: random.Random, pairs):
    values, weights = zip(*pairs)
    return rng.choices(values, weights=weights, k=1)[0]


def maybe_null(rng: random.Random, value, p: float):
    return None if rng.random() < p else value


def rand_ts(rng: random.Random, start: datetime, end: datetime) -> datetime:
    span = int((end - start).total_seconds() * 1_000_000)
    return start + timedelta(microseconds=rng.randrange(span))


# ----------------------------------------------------------------------------
# Entidades maestras
# ----------------------------------------------------------------------------
def build_employees(fake: Faker, rng: random.Random, n: int):
    employees, used_users = [], set()
    for i in range(n):
        first, last1, last2 = fake.first_name(), fake.last_name(), fake.last_name()
        base_user = ascii_slug(first[0] + last1).replace(" ", "")
        user, k = base_user, 1
        while user in used_users:
            k += 1
            user = f"{base_user}{k}"
        used_users.add(user)
        dept_idx = rng.randrange(len(DEPARTAMENTOS))
        employees.append({
            "cve_empleado": 100000 + i * 7 + rng.randrange(7),
            "nm_empleado": f"{first} {last1} {last2}".upper(),
            "correo_electronico": f"{ascii_slug(first).replace(' ', '.')}.{ascii_slug(last1).replace(' ', '')}"
                                  f"{'' if k == 1 else k}@{EMAIL_DOMAIN}",
            "usr_red": user,
            "negocio": rng.choice(NEGOCIOS),
            "estatus_empleado": rng.choice(ESTATUS_EMPLEADO),
            "nm_posicion": rng.choice(POSICIONES),
            "nm_ceco": CECOS[dept_idx],
            "nm_departamento": DEPARTAMENTOS[dept_idx],
            "nm_funcion": rng.choice(FUNCIONES),
            "dominio": rng.choice(DOMINIOS).split(".")[0].upper(),
        })
    # asignar jefes (otro empleado; los primeros 5% son jefes de si mismos -> nivel directivo)
    bosses = employees[: max(1, n // 20)]
    for e in employees:
        e["nm_jefe"] = rng.choice(bosses)["nm_empleado"]
    return employees


def build_devices(rng: random.Random, employees, n: int):
    devices, names = [], set()
    for _ in range(n):
        os_name = weighted(rng, [("Windows", 72), ("macOS", 20), ("Linux", 8)])
        prefix = {"Windows": "MXL", "macOS": "MAC", "Linux": "LNX"}[os_name]
        while True:
            name = f"{prefix}{rng.randrange(10**6):06d}{rng.choice('ABCDEFGHJKLMNPQRSTUVWXYZ')}"
            if name not in names:
                names.add(name)
                break
        owner = rng.choice(employees)
        devices.append({
            "nm_computadora": name,
            "sistema_operativo": os_name,
            "version_so": rng.choice(OS_VERSIONS[os_name]),
            "dominio": rng.choice(DOMINIOS),
            "ipv4": f"10.{rng.randrange(1, 255)}.{rng.randrange(0, 255)}.{rng.randrange(1, 255)}",
            "owner": owner,
            "cve_instancia": f"INS-{rng.randrange(10**5):05d}",
            "id_instancia": f"i-{uuid.UUID(int=rng.getrandbits(128)).hex[:17]}",
        })
    return devices


# ----------------------------------------------------------------------------
# db_production
# ----------------------------------------------------------------------------
def gen_cat_valor_sensor():
    return [dict(zip(["cve_valor_sensor", "valor_sensor", "valor_sensor_descripcion"], r))
            for r in CAT_VALOR_SENSOR]


def sensor_cve(rng: random.Random, sensor: str, os_name: str) -> str:
    if os_name not in SENSORES_PROD[sensor]:
        return "4"
    return weighted(rng, CVE_WEIGHTS)


def gen_logs(rng: random.Random, devices, n: int, start: date, end: date):
    rows = []
    start_dt = datetime.combine(start, datetime.min.time())
    end_dt = datetime.combine(end, datetime.max.time())
    # la certificacion on-premise solo cubre estaciones Windows / macOS
    endpoints = [d for d in devices if d["sistema_operativo"] in ("Windows", "macOS")]
    for i in range(n):
        d = rng.choice(endpoints)
        emp = d["owner"]
        os_name = d["sistema_operativo"]
        fch_rec = rand_ts(rng, start_dt, end_dt)
        fch_reg = fch_rec.date()
        iso = fch_reg.isocalendar()
        cves = {s: sensor_cve(rng, s, os_name) for s in SENSORES_PROD}
        cumple = all(c in CVE_CUMPLE for c in cves.values())
        estado = "CERTIFICADO" if cumple else "NO CERTIFICADO"
        recert = "S" if rng.random() < 0.12 else "N"
        last_user = rng.choice([emp["usr_red"], emp["usr_red"], rng.choice(["admin.local", "soporte.ti"])])
        rows.append({
            "fch_gs_registro": fch_reg,
            "semana_gs_registro": f"{iso[0]}-W{iso[1]:02d}",
            "mes_gs_registro": fch_reg.strftime("%Y-%m"),
            "cve_empleado": str(emp["cve_empleado"]),
            "nm_empleado": emp["nm_empleado"],
            "correo_electronico": emp["correo_electronico"],
            "usr_red": emp["usr_red"],
            "negocio": emp["negocio"],
            "estatus_empleado": emp["estatus_empleado"],
            "nm_posicion": emp["nm_posicion"],
            "nm_ceco": emp["nm_ceco"],
            "nm_jefe": emp["nm_jefe"],
            "num_log": f"LOG{fch_reg:%Y%m%d}{i:08d}",
            "prioridad": rng.choice(PRIORIDADES),
            "version": rng.choice(["1.0", "1.1", "2.0", "2.1"]),
            "fch_recepcion": fch_rec,
            "nm_servidor": rng.choice(SERVIDORES),
            "recurso": rng.choice(RECURSOS),
            "id_proceso": str(rng.randrange(1000, 65535)),
            "nm_computadora": d["nm_computadora"],
            "nm_dominio_computadora": d["dominio"],
            "sistema_operativo": os_name,
            "version_sistema_operativo": d["version_so"],
            "nm_usr": emp["usr_red"],
            "nm_ultimo_usr_ini_sesion": maybe_null(rng, last_user, 0.02),
            "direccion_ipv4": maybe_null(rng, d["ipv4"], 0.01),
            **{f"cve_estado_{s}": c for s, c in cves.items()},
            "estado_windows": estado if os_name == "Windows" else "NO APLICA",
            "estado_mac": estado if os_name == "macOS" else "NO APLICA",
            "etiquetas_personalizadas_descr": maybe_null(
                rng, ", ".join(rng.sample(ETIQUETAS, rng.randint(1, 3))), 0.55),
            "indicador_recertificacion": recert,
            "recertificacion_descripcion": (
                rng.choice(["Recertificación por cambio de usuario", "Recertificación por reinstalación de SO",
                            "Recertificación solicitada por auditoría", "Recertificación por excepción vencida"])
                if recert == "S" else None),
            "tiempo": f"{rng.randrange(0, 1):02d}:{rng.randrange(0, 15):02d}:{rng.randrange(0, 60):02d}",
            "consola": rng.choice(CONSOLAS),
            "audit_fch_carga": fch_rec + timedelta(hours=rng.randint(1, 30), microseconds=rng.randrange(10**6)),
        })
    return rows


def derive_vw_logs(logs):
    rows = []
    for l in logs:
        r = {
            "fch_gs_registro": l["fch_gs_registro"],
            "fch_recepcion": l["fch_recepcion"],
            "nm_computadora": l["nm_computadora"],
            "sistema_operativo": l["sistema_operativo"],
            "estado_windows": l["estado_windows"],
            "estado_mac": l["estado_mac"],
        }
        cumple_all = True
        for s in ["crowd_strike", "sep_antivirus", "carbon_black", "symantec_dlp_wdp", "symantec_dlp_seha",
                  "symantec_dlp_edpa", "jamf_pro_mac", "tanium"]:
            cve = l[f"cve_estado_{s}"]
            ok = cve in CVE_CUMPLE
            cumple_all &= ok
            r[f"valor_{s}"] = CAT_BY_CVE[cve][0]
            r[f"estado_{s}"] = "CUMPLE" if ok else "NO CUMPLE"
        r["estado_certificacion"] = "CERTIFICADO" if cumple_all else "NO CERTIFICADO"
        rows.append(r)
    return rows


# ----------------------------------------------------------------------------
# db_consulting
# ----------------------------------------------------------------------------
def gen_sensores_cloud():
    return [{"sensor_key": s, "sensor_value": v, "sensor_status": st}
            for s in SENSORES_CLOUD for v, st in CLOUD_VALUES]


def gen_equipos_cloud(rng: random.Random, devices, n: int, start: date, end: date):
    rows = []
    start_dt = datetime.combine(start, datetime.min.time())
    end_dt = datetime.combine(end, datetime.max.time())
    for i in range(n):
        d = rng.choice(devices)
        emp = d["owner"]
        os_name = d["sistema_operativo"]
        fch = rand_ts(rng, start_dt, end_dt)
        values = {s: ("N/A" if os_name not in SENSORES_CLOUD[s] else weighted(rng, CLOUD_WEIGHTS))
                  for s in SENSORES_CLOUD}
        sumatoria = sum(1 for v in values.values() if CLOUD_STATUS[v] == "OK")
        row = {
            "fch_peticion": fch,
            "nm_equipo": d["nm_computadora"],
            "dominio_equipo": d["dominio"],
            "os_version": d["version_so"],
            "os_tipo": rng.choice(OS_TIPO_CLOUD[os_name]),  # sucio a proposito: se normaliza en la vw
            "usr_sensado_actual": emp["usr_red"],
            "usr_sensado_anterior": maybe_null(rng, rng.choice([emp["usr_red"], "admin.local", "soporte.ti"]), 0.15),
            "ip_address": maybe_null(rng, d["ipv4"], 0.01),
            "tenant_mdr": rng.choice(TENANTS_MDR),
            "contador": rng.randint(1, 50),
            "age": rng.randint(0, 90),
            "sumatoria": sumatoria,
            "estado_de_certificacion": "CERTIFICADO" if sumatoria == len(SENSORES_CLOUD) else "NO CERTIFICADO",
            "cve_empleado": emp["cve_empleado"],
            "dominio": emp["dominio"],
            "nm_empleado": emp["nm_empleado"],
            "correo_electronico": emp["correo_electronico"],
            "estatus_empleado": emp["estatus_empleado"],
            "cve_instancia": d["cve_instancia"],
            "id_instancia": d["id_instancia"],
            "nm_ceco": emp["nm_ceco"],
            "nm_funcion": emp["nm_funcion"],
            "nm_jefe": emp["nm_jefe"],
            "nm_posicion": emp["nm_posicion"],
            "nm_departamento": emp["nm_departamento"],
            "audit_path": f"/data/landing/equipos_cloud/{fch:%Y/%m/%d}/equipos_cloud_{fch:%Y%m%d}.csv",
            "audit_usr": rng.choice(["svc_etl_cloud", "svc_etl_batch"]),
            "audit_fch_carga": fch + timedelta(hours=rng.randint(1, 12), microseconds=rng.randrange(10**6)),
        }
        for col, est_col in CLOUD_COLS.items():
            row[col] = values[col]
            row[est_col] = CLOUD_STATUS[values[col]]
        rows.append(row)
    return rows


def derive_vw_equipos_cloud(equipos):
    norm_by_tipo = {t: OS_NORM[os] for os, tipos in OS_TIPO_CLOUD.items() for t in tipos}
    cols = ["fch_peticion", "nm_equipo", "os_tipo", "estado_de_certificacion",
            *[c for pair in CLOUD_COLS.items() for c in pair]]
    return [{**{c: e[c] for c in cols}, "os_tipo_norm": norm_by_tipo[e["os_tipo"]]} for e in equipos]


# ----------------------------------------------------------------------------
# Escritura
# ----------------------------------------------------------------------------
def write_table(out: Path, db: str, table: str, rows) -> int:
    schema = schemas.TABLES[(db, table)]
    data = {f.name: [r.get(f.name) for r in rows] for f in schema}
    arrow_tbl = pa.Table.from_pydict(data, schema=schema)
    tdir = out / db / table
    if tdir.exists():
        shutil.rmtree(tdir)
    tdir.mkdir(parents=True)
    # INT96 para TIMESTAMP: formato que Hive e Impala leen de forma nativa
    pq.write_table(arrow_tbl, tdir / "part-00000.parquet", compression="snappy",
                   use_deprecated_int96_timestamps=True)
    return arrow_tbl.num_rows


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", default="data/source", help="directorio de salida")
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--scale", type=float, default=1.0,
                    help="multiplicador de volumen (1.0 = 800 empleados, 1500 equipos, 20k logs, 8k cloud)")
    ap.add_argument("--start-date", default="2026-01-01")
    ap.add_argument("--end-date", default="2026-10-31")
    args = ap.parse_args()

    rng = random.Random(args.seed)
    fake = Faker("es_MX")
    fake.seed_instance(args.seed)
    start, end = date.fromisoformat(args.start_date), date.fromisoformat(args.end_date)
    n = lambda base: max(1, int(base * args.scale))  # noqa: E731

    employees = build_employees(fake, rng, n(800))
    devices = build_devices(rng, employees, n(1500))
    logs = gen_logs(rng, devices, n(20_000), start, end)
    equipos = gen_equipos_cloud(rng, devices, n(8_000), start, end)

    datasets = {
        ("db_production", "cat_certificacion_valor_sensor"): gen_cat_valor_sensor(),
        ("db_production", "tbl_certificacion_logs"): logs,
        ("db_production", "vw_certificacion_logs"): derive_vw_logs(logs),
        ("db_consulting", "tbl_sensores_equipos_cloud"): gen_sensores_cloud(),
        ("db_consulting", "tbl_equipos_cloud_certificacion"): equipos,
        ("db_consulting", "vw_equipos_cloud_certificacion"): derive_vw_equipos_cloud(equipos),
    }

    out = Path(args.out)
    manifest = {"seed": args.seed, "scale": args.scale, "start_date": args.start_date,
                "end_date": args.end_date, "generated_at": datetime.now().isoformat(timespec="seconds"),
                "tables": {}}
    for (db, table), rows in datasets.items():
        count = write_table(out, db, table, rows)
        manifest["tables"][f"{db}.{table}"] = {
            "rows": count,
            "path": f"{db}/{table}/part-00000.parquet",
            "columns": [{"name": f.name, "type": str(f.type)} for f in schemas.TABLES[(db, table)]],
        }
        print(f"  {db}.{table:<34} {count:>8,} filas")
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"Dataset escrito en {out.resolve()}")


if __name__ == "__main__":
    main()
