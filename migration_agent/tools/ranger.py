"""FASE 3b: traduccion de politicas Apache Ranger (hive/impala) -> control de acceso VAST via Trino.

VAST 5.5 no documenta GRANT/REVOKE. El control de acceso se modela con:
  * Identity policies en VMS (niveles read / write / admin sobre bucket/schema/tabla).
  * Reglas de fila/columna de VAST que Trino aplica (column allow/deny, column mask, row filter)
    cuando el catalogo tiene enable_access_control / enable_row_column_security /
    enable_end_user_impersonation = true.
Se generan dos artefactos:
  1. vast_access_plan.json  -> especificacion para configurar en VMS (revision humana obligatoria).
  2. trino_rules.json       -> Trino file-based access control equivalente (opcional, si Trino es el
                               punto de control o para el ambiente de emulacion).
"""
from __future__ import annotations

import sqlglot

from ..core.ir import Finding, Inventory, RangerPolicy, Severity
from ..core.matrix import EquivalenceMatrix
from .translate import TargetNaming

_LEVEL_RANK = {"read": 1, "write": 2, "admin": 3}
_TRINO_PRIVS = {"read": ["SELECT"], "write": ["SELECT", "INSERT", "UPDATE", "DELETE"],
                "admin": ["SELECT", "INSERT", "UPDATE", "DELETE", "OWNERSHIP"]}


def _to_trino(expr: str) -> str:
    """Expresiones Ranger estan en sintaxis Hive (p. ej. arrays base 0): se transpilan a Trino."""
    try:
        return sqlglot.transpile(expr, read="hive", write="trino")[0]
    except Exception:  # noqa: BLE001
        return expr


def _regex(values: list[str]) -> str:
    if not values or "*" in values:
        return ".*"
    pats = [v.replace(".", r"\.").replace("*", ".*") for v in values]
    return pats[0] if len(pats) == 1 else "(" + "|".join(pats) + ")"


def translate_policies(inv: Inventory, matrix: EquivalenceMatrix, naming: TargetNaming
                       ) -> tuple[dict, dict, list[Finding]]:
    findings: list[Finding] = []
    levels = matrix.security.get("access_levels", {})
    masks = matrix.security.get("masks", {})
    plan = {"model": matrix.security.get("model", ""), "catalog_flags": {
        "enable_access_control": "true", "enable_row_column_security": "true",
        "enable_end_user_impersonation": "true"},
        "identity_policies": [], "column_rules": [], "row_filters": [], "unmapped": []}
    rules = {"catalogs": [{"catalog": naming.catalog, "allow": "all"}], "schemas": [], "tables": []}

    for p in inv.policies:
        obj = f"ranger:{p.service}/{p.name}"
        if not p.is_enabled:
            findings.append(Finding(Severity.INFO, "ranger.disabled", "Politica deshabilitada: no se migra", obj))
            continue
        schemas = [naming.schema(d) for d in (p.databases or ["*"])]
        targets = {
            "bucket": naming.bucket,
            "schemas": schemas,
            "tables": p.tables or ["*"],
            "columns": p.columns or ["*"],
        }
        schema_rx = _regex([f"{naming.bucket}/{x}" if naming.naming == "vast" else x for x in schemas])
        trino_scope = {"catalog": naming.catalog, "schema": schema_rx}
        if p.policy_type == "access":
            _access(p, targets, levels, plan, rules, findings, obj, naming)
        elif p.policy_type == "masking":
            _masking(p, targets, masks, plan, rules, findings, obj, trino_scope)
        elif p.policy_type == "rowfilter":
            _rowfilter(p, targets, plan, rules, findings, obj, trino_scope)

    if inv.policies:
        findings.append(Finding(Severity.WARNING, "ranger.identity_mapping",
                                "Usuarios/grupos Ranger deben existir como identidades VAST (el usuario de sesion "
                                "Trino debe coincidir con el login VAST). Grupos se expanden en VMS.", "ranger"))
    return plan, rules, findings


def _principals(item: dict) -> dict:
    return {"users": item.get("users", []), "groups": item.get("groups", []), "roles": item.get("roles", [])}


def _access(p: RangerPolicy, targets: dict, levels: dict, plan: dict, rules: dict,
            findings: list[Finding], obj: str, naming: TargetNaming) -> None:
    for it in p.items:
        if it["kind"] not in ("allow", "deny"):
            if it["kind"].endswith("exception"):
                findings.append(Finding(Severity.MANUAL, "ranger.exceptions",
                                        "Excepciones allow/deny no tienen equivalente directo: revisar", obj))
            continue
        lvl = max((levels.get(a, None) for a in it["accesses"] if levels.get(a)),
                  key=lambda x: _LEVEL_RANK[x], default=None)
        unknown = [a for a in it["accesses"] if a not in levels]
        if unknown:
            plan["unmapped"].append({"policy": p.name, "accesses": unknown})
            findings.append(Finding(Severity.WARNING, "ranger.access_unmapped", f"Accesos sin mapeo: {unknown}", obj))
        if not lvl:
            continue
        effect = "deny" if it["kind"] == "deny" else "allow"
        plan["identity_policies"].append({
            "source_policy": p.name, "effect": effect, "level": lvl, **_principals(it), **targets})
        if targets["columns"] != ["*"]:
            plan["column_rules"].append({"source_policy": p.name, "type": "column_" + effect,
                                         "columns": targets["columns"], **_principals(it),
                                         **{k: targets[k] for k in ("bucket", "schemas", "tables")}})
        for who_key, who_vals in (("user", it.get("users", [])), ("group", it.get("groups", []))):
            for who in who_vals:
                rule = {who_key: who, "catalog": naming.catalog,
                        "schema": _regex([f"{naming.bucket}/{s}" if naming.naming == "vast" else s
                                          for s in targets["schemas"]]),
                        "table": _regex(targets["tables"]),
                        "privileges": [] if effect == "deny" else _TRINO_PRIVS[lvl]}
                if targets["columns"] != ["*"] and effect == "allow":
                    findings.append(Finding(Severity.INFO, "ranger.column_allow",
                                            "Permiso por columnas: en Trino se modela con deny del resto de columnas", obj))
                if targets["columns"] != ["*"] and effect == "deny":
                    rule["privileges"] = _TRINO_PRIVS["read"]
                    rule["columns"] = [{"name": c, "allow": False} for c in targets["columns"]]
                rules["tables"].append(rule)
        if "public" in [g.lower() for g in it.get("groups", [])]:
            findings.append(Finding(Severity.WARNING, "ranger.public_group",
                                    "Grupo 'public' de Ranger: definir equivalente (todos los usuarios) en VAST", obj))


def _masking(p: RangerPolicy, targets: dict, masks: dict, plan: dict, rules: dict,
             findings: list[Finding], obj: str, scope: dict) -> None:
    for it in p.items:
        if it["kind"] != "mask":
            continue
        mtype = it.get("mask_type") or "MASK"
        for col in targets["columns"]:
            if mtype == "CUSTOM":
                expr = _to_trino((it.get("mask_expr") or "").replace("{col}", col))
                findings.append(Finding(Severity.WARNING, "ranger.mask_custom",
                                        f"Mascara CUSTOM '{it.get('mask_expr')}': validar sintaxis Trino", obj))
            elif mtype in masks:
                expr = masks[mtype].format(col=col) if masks[mtype] else None
            else:
                expr = None
                findings.append(Finding(Severity.MANUAL, "ranger.mask_unknown", f"Tipo de mascara {mtype}", obj))
            if expr is None:
                continue
            plan["column_rules"].append({"source_policy": p.name, "type": "column_mask", "column": col,
                                         "mask_type": mtype, "expression": expr, **_principals(it),
                                         **{k: targets[k] for k in ("bucket", "schemas", "tables")}})
            for u in it.get("users", []):
                rules["tables"].append({"user": u, **scope, "table": _regex(targets["tables"]),
                                        "privileges": ["SELECT"], "columns": [{"name": col, "mask": expr}]})
            for g in it.get("groups", []):
                rules["tables"].append({"group": g, **scope, "table": _regex(targets["tables"]),
                                        "privileges": ["SELECT"], "columns": [{"name": col, "mask": expr}]})


def _rowfilter(p: RangerPolicy, targets: dict, plan: dict, rules: dict, findings: list[Finding], obj: str,
               scope: dict) -> None:
    for it in p.items:
        if it["kind"] != "rowfilter" or not it.get("filter_expr"):
            continue
        expr = _to_trino(it["filter_expr"])
        findings.append(Finding(Severity.WARNING, "ranger.rowfilter",
                                f"Filtro '{expr}' en sintaxis Hive: validar que sea SQL Trino valido", obj))
        plan["row_filters"].append({"source_policy": p.name, "filter": expr, **_principals(it),
                                    **{k: targets[k] for k in ("bucket", "schemas", "tables")}})
        for u in it.get("users", []):
            rules["tables"].append({"user": u, **scope, "table": _regex(targets["tables"]),
                                    "privileges": ["SELECT"], "filter": expr})
        for g in it.get("groups", []):
            rules["tables"].append({"group": g, **scope, "table": _regex(targets["tables"]),
                                    "privileges": ["SELECT"], "filter": expr})
