"""FASE 5: validacion de la migracion.

Genera un paquete de verificacion (consultas + resultados esperados). En modo destino online las
ejecuta via Trino y compara; en modo offline quedan listas para ejecucion manual en VAST.
Si el origen tambien esta online, los resultados esperados se obtienen del propio origen (Hive).
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

from ..core.ir import Inventory, Table


@dataclass
class Check:
    id: str
    table: str
    kind: str            # row_count | null_count | min | max | distinct_count
    column: str | None
    sql: str
    expected: str | int | None = None
    actual: str | int | None = None
    status: str = "pending"   # pending | pass | fail | error

    def to_dict(self) -> dict:
        return asdict(self)


def build_checks(inv: Inventory, expected: dict, table_ref) -> list[Check]:
    """expected: manifest['expected'] del generador sintetico o perfil obtenido del origen."""
    checks: list[Check] = []
    n = 0
    for fqn, prof in expected.items():
        t: Table | None = inv.get_table(fqn)
        if t is None:
            continue
        ref = table_ref(t)
        n += 1
        checks.append(Check(f"C{n:04d}", fqn, "row_count", None, f"SELECT COUNT(*) FROM {ref}", prof["rows"]))
        for col, info in prof.get("columns", {}).items():
            c = t.column(col)
            if c is None or (c.target_type or "").upper().startswith(("ARRAY", "MAP", "ROW")):
                continue
            n += 1
            checks.append(Check(f"C{n:04d}", fqn, "null_count", col,
                                f"SELECT COUNT(*) FROM {ref} WHERE {col} IS NULL", info["nulls"]))
            if "min" in info:
                n += 1
                checks.append(Check(f"C{n:04d}", fqn, "min", col, f"SELECT CAST(MIN({col}) AS VARCHAR) FROM {ref}",
                                    info["min"]))
                n += 1
                checks.append(Check(f"C{n:04d}", fqn, "max", col, f"SELECT CAST(MAX({col}) AS VARCHAR) FROM {ref}",
                                    info["max"]))
            if info.get("semantic") in ("code", "hostname", "category", "status"):
                n += 1
                checks.append(Check(f"C{n:04d}", fqn, "distinct_count", col,
                                    f"SELECT COUNT(DISTINCT {col}) FROM {ref}", info["distinct"]))
    return checks


def compare(expected, actual) -> bool:
    if expected is None:
        return True
    e, a = str(expected).strip(), str(actual).strip()
    if e == a:
        return True
    # tolerancia de formato de timestamps / decimales ("2026-01-01 00:00:00.000000" vs "2026-01-01 00:00:00.000")
    try:
        return abs(float(e) - float(a)) < 1e-6
    except ValueError:
        return e.rstrip("0").rstrip(".") == a.rstrip("0").rstrip(".") or e[:19] == a[:19]


def render_checks_sql(checks: list[Check]) -> str:
    lines = ["-- Paquete de validacion de la migracion (ejecutar con el cliente Trino contra VAST).",
             "-- Compare cada resultado con 'expected' en expected_results.json.\n"]
    for c in checks:
        lines.append(f"-- {c.id} | {c.table} | {c.kind}{(' ' + c.column) if c.column else ''} | esperado: {c.expected}")
        lines.append(c.sql + ";\n")
    return "\n".join(lines)
