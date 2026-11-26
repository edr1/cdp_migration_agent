"""FASE 1 (offline): ingesta del paquete de entrada (DDL, DML, DQL, politicas Ranger) -> Inventory.

Estructura esperada del paquete (todas las carpetas son opcionales; tambien se aceptan .sql sueltos):
    <bundle>/ddl/*.sql      <bundle>/dml/*.sql      <bundle>/dql/*.sql
    <bundle>/ranger/*.json  (export de Ranger: {"policies": [...]} o lista de politicas)
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from ..core.ir import Finding, Inventory, RangerPolicy, Severity, StatementKind
from .sql_parsing import make_statement, split_statements, table_from_ddl

_POLICY_TYPES = {0: "access", 1: "masking", 2: "rowfilter", "0": "access", "1": "masking", "2": "rowfilter"}


def ingest_bundle(bundle: str | Path) -> tuple[Inventory, list[Finding]]:
    bundle = Path(bundle)
    inv = Inventory(origin="offline")
    findings: list[Finding] = []
    if not bundle.exists():
        raise FileNotFoundError(f"No existe el paquete de entrada: {bundle}")

    sql_files = sorted(bundle.rglob("*.sql"), key=lambda p: (_phase_order(p, bundle), str(p)))
    idx = 0
    for f in sql_files:
        current_db = "default"
        engine = "impala" if "impala" in f.name.lower() else "hive"
        rel = str(f.relative_to(bundle)).replace("\\", "/")
        for raw in split_statements(f.read_text(encoding="utf-8")):
            idx += 1
            st = make_statement(idx, raw, rel)
            m = re.match(r"^\s*USE\s+`?(\w+)`?", raw, re.I)
            if m:
                current_db = m.group(1).lower()
            # marcador de motor dentro del script ("-- impala" / "#------ Impala")
            inv.statements.append(st)
            if st.operation in ("CREATE_TABLE", "CREATE_VIEW", "CTAS"):
                table, fnd = table_from_ddl(raw, current_db, engine)
                for x in fnd:
                    x.statement_id = st.id
                st.findings.extend(fnd)
                if table:
                    prev = inv.get_table(table.fqn)
                    if prev is not None:
                        findings.append(Finding(Severity.WARNING, "inventory.duplicate_table",
                                                f"{table.fqn} definida mas de una vez; se conserva la ultima ({rel})",
                                                table.fqn, st.id))
                    inv.add_table(table)
            elif st.operation == "CREATE_DATABASE":
                m2 = re.search(r"(DATABASE|SCHEMA)\s+(IF\s+NOT\s+EXISTS\s+)?`?(\w+)`?", raw, re.I)
                if m2 and m2.group(3).lower() not in inv.databases:
                    inv.databases.append(m2.group(3).lower())
            elif st.kind == StatementKind.UNKNOWN:
                st.findings.append(Finding(Severity.MANUAL, "parse.unknown",
                                           "Sentencia no reconocida", statement_id=st.id))

    for jf in sorted(bundle.rglob("*.json")):
        try:
            inv.policies.extend(parse_ranger_export(json.loads(jf.read_text(encoding="utf-8"))))
        except (json.JSONDecodeError, KeyError, TypeError) as e:
            findings.append(Finding(Severity.WARNING, "ranger.parse", f"{jf.name}: {e}"))
    return inv, findings


def _phase_order(p: Path, root: Path) -> int:
    parts = [x.lower() for x in p.relative_to(root).parts]
    for i, key in enumerate(("ddl", "dml", "dql")):
        if key in parts:
            return i
    return 3


def parse_ranger_export(data) -> list[RangerPolicy]:
    """Acepta el export JSON de Ranger (Admin UI / REST v2) o una lista de politicas."""
    items = data.get("policies", data) if isinstance(data, dict) else data
    out: list[RangerPolicy] = []
    for p in items:
        res = p.get("resources", {})
        ptype = _POLICY_TYPES.get(p.get("policyType", 0), "access")
        entries = []
        for key, kind in (("policyItems", "allow"), ("denyPolicyItems", "deny"),
                          ("allowExceptions", "allow_exception"), ("denyExceptions", "deny_exception"),
                          ("dataMaskPolicyItems", "mask"), ("rowFilterPolicyItems", "rowfilter")):
            for it in p.get(key, []) or []:
                entries.append({
                    "kind": kind,
                    "users": it.get("users", []),
                    "groups": it.get("groups", []),
                    "roles": it.get("roles", []),
                    "accesses": [a.get("type") for a in it.get("accesses", []) if a.get("isAllowed", True)],
                    "mask_type": (it.get("dataMaskInfo") or {}).get("dataMaskType"),
                    "mask_expr": (it.get("dataMaskInfo") or {}).get("valueExpr"),
                    "filter_expr": (it.get("rowFilterInfo") or {}).get("filterExpr"),
                })
        out.append(RangerPolicy(
            id=str(p.get("id", p.get("name"))),
            name=p.get("name", ""),
            service=p.get("service", ""),
            policy_type=ptype,
            databases=(res.get("database") or {}).get("values", []),
            tables=(res.get("table") or {}).get("values", []),
            columns=(res.get("column") or {}).get("values", []),
            items=entries,
            is_enabled=p.get("isEnabled", True),
            raw=p,
        ))
    return out
