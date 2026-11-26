"""FASE 4: generacion de datos sinteticos guiada por el esquema (adaptada del patron Faker de Databricks).

Principios (equivalentes a la skill faker-data-generation de Databricks, adaptados a VAST):
  * Reproducible: semilla unica para random y Faker.
  * Realista: distribuciones no uniformes (pesos tipo Zipf, log-normal, Poisson) y estacionalidad semanal.
  * Integridad referencial: tablas padre (catalogos) primero; columnas hijas toman valores del padre;
    columnas homonimas entre tablas comparten un pool de valores (joins consistentes).
  * Valido primero, corrupcion despues: corruption_rate inyecta registros invalidos y cada uno queda
    registrado con la validacion que deberia detectarlo (corruption -> expectation).
  * Solo datos crudos: no se generan agregados.
Salidas: Parquet / CSV / scripts INSERT en sintaxis Trino-VAST (lotes < 5 MB) + manifest con
resultados esperados (conteos, nulos, min/max, distintos) para validar la migracion manualmente.
"""
from __future__ import annotations

import csv
import json
import math
import random
import re
import uuid
import zlib
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from ..core.ir import Column, Inventory, Table

# --------------------------------------------------------------------------- semantica
# (regex sobre el nombre de columna, semantica). Se evalua en orden; la primera que coincide gana.
SEMANTIC_RULES: list[tuple[str, str]] = [
    (r"(correo|email|e_mail|mail)", "email"),
    (r"^(nm_|nombre_)?(empleado|cliente|persona|usuario_nombre|jefe)$|^nm_(empleado|jefe|cliente)$", "person_name"),
    (r"(usr|user|usuario|login)", "username"),
    (r"(ipv4|ip_address|direccion_ip|^ip$)", "ipv4"),
    (r"(dominio|domain)", "domain"),
    (r"(computadora|equipo|hostname|host|servidor)", "hostname"),
    (r"(uuid|guid|^id_instancia$)", "uuid"),
    (r"^(cve_|id_|cod_|clave_)", "code"),
    (r"(semana)", "iso_week"),
    (r"^mes_|_mes$|(month)", "year_month"),
    (r"(estatus|estado|status)", "status"),
    (r"(indicador|flag|^is_|^es_)", "flag"),
    (r"(prioridad|priority)", "priority"),
    (r"(posicion|puesto|cargo|job)", "job"),
    (r"(version)", "version"),
    (r"(path|ruta|location)", "path"),
    (r"(descripcion|descr|comentario|observacion|etiquetas)", "text"),
    (r"(contador|count|cantidad|qty|num_)", "count"),
    (r"(^age$|edad|antiguedad)", "age"),
    (r"(monto|importe|amount|precio|price|saldo|total)", "amount"),
    (r"(tiempo|duracion)", "duration"),
    (r"(negocio|categoria|tipo|segmento|region|area|departamento|funcion|posicion|ceco|consola|recurso|tenant|os_)",
     "category"),
]


def infer_semantic(col: Column) -> str:
    name = col.name.lower()
    t = (col.target_type or col.source_type or "VARCHAR").upper()
    if t.startswith("DATE"):
        return "date"
    if t.startswith("TIMESTAMP"):
        return "timestamp"
    if t.startswith("BOOLEAN"):
        return "boolean"
    if t.startswith(("ARRAY", "MAP", "ROW", "VARBINARY")):
        return "complex"
    for pat, sem in SEMANTIC_RULES:
        if re.search(pat, name):
            if sem in ("count", "age", "amount") and not _is_numeric(t):
                continue
            return sem
    return "number" if _is_numeric(t) else "category"


def _is_numeric(t: str) -> bool:
    return t.startswith(("INTEGER", "BIGINT", "SMALLINT", "TINYINT", "DOUBLE", "REAL", "DECIMAL"))


# --------------------------------------------------------------------------- config
@dataclass
class Relationship:
    child: str          # db.tabla.columna  (la columna admite regex: db.tabla.cve_estado_.*)
    parent: str         # db.tabla.columna
    weights: dict | None = None   # distribucion opcional {valor_padre: peso}


@dataclass
class SynthConfig:
    seed: int = 42
    locale: str = "es_MX"
    rows_default: int = 1000
    rows_per_table: dict[str, int] = field(default_factory=dict)
    corruption_rate: float = 0.0
    start_date: date = date(2026, 1, 1)
    end_date: date = date(2026, 10, 31)
    relationships: list[Relationship] = field(default_factory=list)
    column_overrides: dict[str, dict] = field(default_factory=dict)  # db.t.col -> {semantic, values, weights, null_rate}
    catalog_max_rows: int = 50

    @classmethod
    def from_dict(cls, d: dict) -> "SynthConfig":
        return cls(
            seed=int(d.get("seed", 42)), locale=d.get("locale", "es_MX"),
            rows_default=int(d.get("rows_default", 1000)), rows_per_table=d.get("rows_per_table") or {},
            corruption_rate=float(d.get("corruption_rate", 0.0)),
            start_date=date.fromisoformat(str(d.get("start_date", "2026-01-01"))),
            end_date=date.fromisoformat(str(d.get("end_date", "2026-10-31"))),
            relationships=[Relationship(**r) for r in d.get("relationships", [])],
            column_overrides=d.get("column_overrides") or {},
        )


# --------------------------------------------------------------------------- generador
class SyntheticGenerator:
    def __init__(self, inv: Inventory, cfg: SynthConfig):
        from faker import Faker
        self.inv = inv
        self.cfg = cfg
        self.rng = random.Random(cfg.seed)
        self.fake = Faker(cfg.locale)
        self.fake.seed_instance(cfg.seed)
        self.pools: dict[str, list] = {}          # nombre de columna -> pool compartido
        self.data: dict[str, list[dict]] = {}     # fqn -> filas
        self.corruptions: list[dict] = []

    # ---------------- planificacion -----------------
    def tables(self) -> list[Table]:
        return [t for t in self.inv.tables.values() if not t.is_view or t.columns]

    def is_catalog(self, t: Table) -> bool:
        return bool(re.match(r"^(cat_|ctl_|dim_|lkp_)", t.name)) or (
            len(t.columns) <= 4 and re.search(r"(sensor|catalog|tipo|valor)", t.name) is not None)

    def row_target(self, t: Table) -> int:
        if t.fqn in self.cfg.rows_per_table:
            return int(self.cfg.rows_per_table[t.fqn])
        if self.is_catalog(t):
            sizes = [len(v["values"]) for k, v in self.cfg.column_overrides.items()
                     if k.startswith(t.fqn + ".") and v.get("values")]
            return min(self.cfg.catalog_max_rows, max(sizes) if sizes else 12)
        return self.cfg.rows_default

    def relationship(self, t: Table, col: Column) -> Relationship | None:
        for r in self.cfg.relationships:
            cdb, ct, ccol = r.child.split(".", 2)
            if f"{cdb}.{ct}".lower() == t.fqn and re.fullmatch(ccol, col.name, re.I):
                return r
        return None

    def parent_of(self, t: Table, col: Column) -> tuple[str, str] | None:
        r = self.relationship(t, col)
        if r is None:
            return None
        pdb, pt, pcol = r.parent.split(".", 2)
        return f"{pdb}.{pt}".lower(), pcol.lower()

    def order(self) -> list[Table]:
        """Padres antes que hijos (catalogos y tablas referidas por relaciones)."""
        parents = {self.parent_of(t, c)[0] for t in self.tables() for c in t.columns if self.parent_of(t, c)}
        return sorted(self.tables(), key=lambda t: (0 if t.fqn in parents or self.is_catalog(t) else
                                                    (2 if t.is_view or t.name.startswith("vw_") else 1), t.fqn))

    # ---------------- generacion -----------------
    def generate(self) -> dict[str, list[dict]]:
        for t in self.order():
            n = self.row_target(t)
            gens = {c.name: self._column_generator(t, c) for c in t.columns}
            rows = []
            for i in range(n):
                row = {name: g(i) for name, g in gens.items()}
                rows.append(row)
            self._apply_coherence(t, rows)
            if self.cfg.corruption_rate > 0 and not self.is_catalog(t):
                self._corrupt(t, rows)
            self.data[t.fqn] = rows
        return self.data

    def _column_generator(self, t: Table, c: Column) -> Callable[[int], Any]:
        ov = self.cfg.column_overrides.get(f"{t.fqn}.{c.name}", {})
        sem = ov.get("semantic") or c.semantic or infer_semantic(c)
        c.semantic = sem
        ttype = (c.target_type or "VARCHAR").upper()
        null_rate = float(ov.get("null_rate", 0.0 if (sem in ("code", "uuid") or c.is_partition
                                                      or self.is_catalog(t)) else 0.02))
        parent = self.parent_of(t, c)

        if ov.get("values") and self.is_catalog(t):
            vals = ov["values"]
            base = lambda i: vals[i % len(vals)]  # noqa: E731  catalogo: cada valor una vez
        elif ov.get("values"):
            vals, w = ov["values"], ov.get("weights")
            base = lambda i: self.rng.choices(vals, weights=w)[0]  # noqa: E731
        elif parent:
            pt, pcol = parent
            pool = sorted({r[pcol] for r in self.data.get(pt, []) if r.get(pcol) is not None}, key=str)
            if not pool:
                pool = self._pool(pcol, ttype, "code", 20)
            rel_w = (self.relationship(t, c).weights or {})
            pw = [float(rel_w.get(str(v), 1.0 if not rel_w else 0.5)) for v in pool]
            base = lambda i: self.rng.choices(pool, weights=pw)[0]  # noqa: E731
        elif self.is_catalog(t) and sem == "code":
            base = lambda i: str(i) if ttype.startswith("VARCHAR") else i  # noqa: E731  claves 0..n
        elif sem in ("code", "hostname", "username", "person_name", "email", "uuid", "ipv4", "domain"):
            # entidades compartidas: misma columna en distintas tablas -> mismo pool (joins validos)
            pool = self._pool(c.name, ttype, sem, self._pool_size(sem))
            weights = _zipf(len(pool), 0.6)
            base = lambda i: self.rng.choices(pool, weights=weights)[0]  # noqa: E731
        else:
            base = self._value_fn(sem, ttype, c.name)

        def gen(i: int) -> Any:
            if null_rate and self.rng.random() < null_rate:
                return None
            return _coerce(base(i), ttype)
        return gen

    def _pool_size(self, sem: str) -> int:
        n = max(10, self.cfg.rows_default // 10)
        return {"domain": 4, "code": n, "hostname": n, "username": n, "person_name": n, "email": n}.get(sem, n)

    def _pool(self, key: str, ttype: str, sem: str, size: int) -> list:
        if key not in self.pools:
            fn = self._value_fn(sem, ttype, key)
            seen, out = set(), []
            for i in range(size * 3):
                v = _coerce(fn(i), ttype)
                if v not in seen:
                    seen.add(v)
                    out.append(v)
                if len(out) >= size:
                    break
            self.pools[key] = out
        return self.pools[key]

    def _value_fn(self, sem: str, ttype: str, name: str) -> Callable[[int], Any]:
        f, r = self.fake, self.rng
        start = datetime.combine(self.cfg.start_date, datetime.min.time())
        span_days = max(1, (self.cfg.end_date - self.cfg.start_date).days)
        cats = self._categories(name)
        cat_w = _zipf(len(cats), 1.1)

        def rand_dt() -> datetime:
            # estacionalidad semanal: fines de semana con 40% del volumen
            while True:
                d = start + timedelta(days=r.randrange(span_days + 1), seconds=r.randrange(86400),
                                      microseconds=r.randrange(1_000_000))
                if d.weekday() < 5 or r.random() < 0.4:
                    return d
        table = {
            "email": lambda i: f.unique.email() if i < 5000 else f.email(),
            "person_name": lambda i: f"{f.first_name()} {f.last_name()} {f.last_name()}".upper(),
            "username": lambda i: f.user_name(),
            "ipv4": lambda i: f.ipv4_private(),
            "domain": lambda i: r.choice(["corp", "mx", "lat", "cloud"]) + ".empresa-demo.local",
            "hostname": lambda i: f"{r.choice(['MXL', 'MAC', 'SRV', 'LNX'])}{r.randrange(10**6):06d}",
            "uuid": lambda i: str(uuid.UUID(int=r.getrandbits(128))),
            "code": lambda i: (r.randrange(100000, 999999) if _is_numeric(ttype) else f"{r.randrange(100000, 999999)}"),
            "iso_week": lambda i: (lambda d: f"{d.isocalendar()[0]}-W{d.isocalendar()[1]:02d}")(rand_dt()),
            "year_month": lambda i: rand_dt().strftime("%Y-%m"),
            "status": lambda i: r.choices(cats, weights=cat_w)[0],
            "flag": lambda i: r.choices(["S", "N"], weights=[15, 85])[0],
            "priority": lambda i: r.choices(["ALTA", "MEDIA", "BAJA"], weights=[15, 50, 35])[0],
            "version": lambda i: r.choice(["1.0", "1.1", "2.0", "2.1"]),
            "path": lambda i: f"/data/landing/{name}/{rand_dt():%Y/%m/%d}/part-{r.randrange(1000):04d}.parquet",
            "text": lambda i: f.sentence(nb_words=6),
            "job": lambda i: f.job().upper()[:60],
            "count": lambda i: max(0, int(_poisson(r, 8))),
            "age": lambda i: int(r.expovariate(1 / 20)) % 365,
            "amount": lambda i: round(math.exp(r.gauss(7, 0.8)), 2),
            "duration": lambda i: f"00:{r.randrange(15):02d}:{r.randrange(60):02d}",
            "number": lambda i: max(0, int(r.lognormvariate(3, 1))),
            "boolean": lambda i: r.random() < 0.7,
            "date": lambda i: rand_dt().date(),
            "timestamp": lambda i: rand_dt(),
            "complex": lambda i: None,
            "category": lambda i: r.choices(cats, weights=cat_w)[0],
        }
        return table.get(sem, table["category"])

    def _categories(self, name: str) -> list[str]:
        n = name.lower()
        presets = {
            "estado": ["CUMPLE", "NO CUMPLE", "NO APLICA"],
            "estatus": ["ACTIVO", "BAJA", "LICENCIA"],
            "status": ["OK", "KO", "N/A"],
            "sistema_operativo": ["Windows", "macOS", "Linux"],
            "os_tipo": ["Windows", "macOS", "Linux"],
            "negocio": ["BANCA", "RETAIL", "SEGUROS", "CORPORATIVO", "TECNOLOGIA"],
        }
        for k, v in presets.items():
            if k in n:
                return v
        base = re.sub(r"^(nm_|cve_|tipo_)", "", n).upper()
        return [f"{base}_{i:02d}" for i in range(1, 9)]

    # ---------------- coherencia y corrupcion -----------------
    def _apply_coherence(self, t: Table, rows: list[dict]) -> None:
        """Reglas de coherencia por fila: fechas de carga >= fechas de evento; date = fecha del timestamp."""
        dcols = [c.name for c in t.columns if c.semantic == "date"]
        tcols = [c.name for c in t.columns if c.semantic == "timestamp"]
        audit = [c for c in tcols if "audit" in c or "carga" in c]
        event = [c for c in tcols if c not in audit]
        weeks = [c.name for c in t.columns if c.semantic == "iso_week"]
        months = [c.name for c in t.columns if c.semantic == "year_month"]
        names = [c.name for c in t.columns if c.semantic == "person_name"]
        emails = [c.name for c in t.columns if c.semantic == "email"]
        for row in rows:
            ref = next((row[x] for x in (event + dcols) if row.get(x) is not None), None)
            if ref is not None:
                for w in weeks:
                    if row.get(w) is not None:
                        iso = ref.isocalendar()
                        row[w] = f"{iso[0]}-W{iso[1]:02d}"
                for mth in months:
                    if row.get(mth) is not None:
                        row[mth] = ref.strftime("%Y-%m")
            if names and emails and row.get(names[0]) and row.get(emails[0]):
                parts = _slug(row[names[0]]).split()
                if len(parts) >= 2:
                    row[emails[0]] = f"{parts[0]}.{parts[1]}@empresa-demo.com.mx"
            if event and dcols and row.get(event[0]) is not None:
                for d in dcols:
                    if row.get(d) is not None:
                        row[d] = row[event[0]].date()
            for a in audit:
                if event and row.get(event[0]) is not None and row.get(a) is not None:
                    row[a] = row[event[0]] + timedelta(hours=self.rng.randint(1, 30),
                                                       microseconds=self.rng.randrange(1_000_000))

    def _corrupt(self, t: Table, rows: list[dict]) -> None:
        for i, row in enumerate(rows):
            if self.rng.random() >= self.cfg.corruption_rate:
                continue
            col = self.rng.choice(t.columns)
            sem, ttype = col.semantic, (col.target_type or "").upper()
            if sem == "email":
                row[col.name], kind, check = "not-an-email", "invalid_format", f"{col.name} LIKE '%@%.%'"
            elif sem in ("count", "number", "amount", "age") and _is_numeric(ttype):
                row[col.name], kind, check = -1, "out_of_range", f"{col.name} >= 0"
            elif sem in ("date", "timestamp"):
                row[col.name] = (datetime(2099, 1, 1) if sem == "timestamp" else date(2099, 1, 1))
                kind, check = "future_date", f"{col.name} <= current_date"
            elif self.parent_of(t, col):
                row[col.name] = _coerce("999999", ttype)
                kind, check = "invalid_fk", f"{col.name} existe en {self.parent_of(t, col)[0]}"
            else:
                row[col.name], kind, check = None, "null_required", f"{col.name} IS NOT NULL"
            self.corruptions.append({"table": t.fqn, "row": i, "column": col.name,
                                     "corruption": kind, "expectation": check})

    # ---------------- salidas -----------------
    def profile(self) -> dict:
        prof = {}
        for fqn, rows in self.data.items():
            t = self.inv.get_table(fqn)
            cols = {}
            for c in t.columns:
                vals = [r[c.name] for r in rows]
                nn = [v for v in vals if v is not None and not isinstance(v, (list, dict))]
                info = {"nulls": len(vals) - len([v for v in vals if v is not None]),
                        "distinct": len(set(map(str, nn))) if nn else 0, "semantic": c.semantic}
                if nn and isinstance(nn[0], (int, float, Decimal, date, datetime)) and not isinstance(nn[0], bool):
                    info["min"], info["max"] = str(min(nn)), str(max(nn))
                cols[c.name] = info
            prof[fqn] = {"rows": len(rows), "columns": cols}
        return prof


# --------------------------------------------------------------------------- escritura
def write_outputs(gen: SyntheticGenerator, out_dir: Path, formats: list[str], table_ref: Callable[[Table], str],
                  batch_rows: int = 500) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    files: dict[str, list[str]] = {}
    for fqn, rows in gen.data.items():
        t = gen.inv.get_table(fqn)
        tdir = out_dir / t.database / t.name
        tdir.mkdir(parents=True, exist_ok=True)
        files[fqn] = []
        if "parquet" in formats:
            _write_parquet(t, rows, tdir / "part-00000.parquet")
            files[fqn].append(str((tdir / "part-00000.parquet").relative_to(out_dir)))
        if "csv" in formats:
            with open(tdir / "data.csv", "w", newline="", encoding="utf-8") as fh:
                w = csv.DictWriter(fh, fieldnames=[c.name for c in t.columns])
                w.writeheader()
                w.writerows(rows)
            files[fqn].append(str((tdir / "data.csv").relative_to(out_dir)))
        if "sql" in formats:
            p = tdir / "insert.sql"
            p.write_text(render_inserts(t, rows, table_ref(t), batch_rows), encoding="utf-8")
            files[fqn].append(str(p.relative_to(out_dir)))
    manifest = {"seed": gen.cfg.seed, "locale": gen.cfg.locale, "corruption_rate": gen.cfg.corruption_rate,
                "generated_at": datetime.now().isoformat(timespec="seconds"),
                "expected": gen.profile(), "corruptions": gen.corruptions, "files": files}
    (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False, default=str),
                                           encoding="utf-8")
    return manifest


def render_inserts(t: Table, rows: list[dict], ref: str, batch_rows: int) -> str:
    cols = [c.name for c in t.columns]
    types = {c.name: (c.target_type or "VARCHAR").upper() for c in t.columns}
    header = f"-- Datos sinteticos para {t.fqn} ({len(rows)} filas). Ejecutar con el cliente Trino contra VAST.\n"
    chunks = [header]
    max_bytes = 4_500_000  # margen bajo el limite de 5 MB por sentencia de VAST
    for start in range(0, len(rows), batch_rows):
        values = []
        size = 0
        for r in rows[start:start + batch_rows]:
            v = "(" + ", ".join(sql_literal(r[c], types[c]) for c in cols) + ")"
            size += len(v.encode())
            if size > max_bytes:
                break
            values.append(v)
        chunks.append(f"INSERT INTO {ref} ({', '.join(cols)}) VALUES\n  " + ",\n  ".join(values) + ";\n")
    return "\n".join(chunks)


def sql_literal(v: Any, ttype: str) -> str:
    if v is None:
        return "NULL"
    if isinstance(v, bool):
        return "TRUE" if v else "FALSE"
    if ttype.startswith("DATE"):
        return f"DATE '{v.isoformat()}'" if isinstance(v, (date, datetime)) else f"DATE '{v}'"
    if ttype.startswith("TIMESTAMP"):
        return f"TIMESTAMP '{v.strftime('%Y-%m-%d %H:%M:%S.%f')}'" if isinstance(v, datetime) else f"TIMESTAMP '{v}'"
    if ttype.startswith("DECIMAL"):
        return f"DECIMAL '{v}'"
    if isinstance(v, (int, float, Decimal)):
        return str(v)
    return "'" + str(v).replace("'", "''") + "'"


def _write_parquet(t: Table, rows: list[dict], path: Path) -> None:
    import pyarrow as pa
    import pyarrow.parquet as pq
    fields, arrays = [], []
    for c in t.columns:
        at = _arrow_type((c.target_type or "VARCHAR").upper())
        fields.append(pa.field(c.name, at))
        arrays.append([r[c.name] for r in rows])
    table = pa.Table.from_arrays([pa.array(a, type=f.type) for a, f in zip(arrays, fields)], schema=pa.schema(fields))
    pq.write_table(table, path, compression="zstd")


def _arrow_type(t: str):
    import pyarrow as pa
    m = re.match(r"DECIMAL\((\d+),\s*(\d+)\)", t)
    if m:
        return pa.decimal128(int(m.group(1)), int(m.group(2)))
    return {
        "INTEGER": pa.int32(), "BIGINT": pa.int64(), "SMALLINT": pa.int16(), "TINYINT": pa.int8(),
        "DOUBLE": pa.float64(), "REAL": pa.float32(), "BOOLEAN": pa.bool_(), "DATE": pa.date32(),
        "VARBINARY": pa.binary(),
    }.get(t.split("(")[0], pa.timestamp("us") if t.startswith("TIMESTAMP") else pa.string())


def _coerce(v: Any, ttype: str) -> Any:
    if v is None:
        return None
    try:
        if ttype.startswith(("INTEGER", "BIGINT", "SMALLINT", "TINYINT")):
            iv = int(v) if not isinstance(v, str) or v.lstrip("-").isdigit() else zlib.crc32(v.encode()) % 100000
            limits = {"TINYINT": 127, "SMALLINT": 32767, "INTEGER": 2**31 - 1}
            lim = limits.get(ttype.split("(")[0])
            return max(-lim, min(lim, iv)) if lim else iv
        if ttype.startswith(("DOUBLE", "REAL")):
            return float(v)
        if ttype.startswith("DECIMAL"):
            s = int(re.search(r",\s*(\d+)", ttype).group(1)) if "," in ttype else 0
            return Decimal(str(round(float(v), s)))
        if ttype.startswith("VARCHAR"):
            return v if isinstance(v, str) else (v.isoformat(sep=" ") if isinstance(v, datetime) else str(v))
    except (TypeError, ValueError):
        return None
    return v


def _slug(text: str) -> str:
    import unicodedata
    return unicodedata.normalize("NFKD", str(text)).encode("ascii", "ignore").decode().lower()


def _zipf(n: int, s: float) -> list[float]:
    return [1.0 / ((k + 1) ** s) for k in range(n)]


def _poisson(r: random.Random, lam: float) -> int:
    k, p, el = 0, 1.0, math.exp(-lam)
    while p > el:
        k += 1
        p *= r.random()
    return k - 1
