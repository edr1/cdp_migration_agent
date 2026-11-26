"""Matriz de equivalencia SQL Hive/Impala -> VAST (Trino) cargada desde YAML."""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from .ir import Finding, Severity

_TYPE_RE = re.compile(r"^\s*([A-Za-z_]+)\s*(?:\((.*)\))?\s*$", re.S)


@dataclass
class TypeMapping:
    target: str | None
    findings: list[Finding]


class EquivalenceMatrix:
    def __init__(self, data: dict[str, Any]):
        self.data = data
        self.types: dict[str, dict] = {k.upper(): v for k, v in data.get("types", {}).items()}
        self.ddl: dict[str, dict] = data.get("ddl", {})
        self.dml: dict[str, dict] = data.get("dml", {})
        self.session: dict[str, Any] = data.get("session", {})
        self.dql: dict[str, Any] = data.get("dql", {})
        self.security: dict[str, Any] = data.get("security", {})
        self.naming: dict[str, str] = data.get("naming", {})
        self.version = data.get("version", "?")

    @classmethod
    def load(cls, path: str | Path) -> "EquivalenceMatrix":
        return cls(yaml.safe_load(Path(path).read_text(encoding="utf-8")))

    # ------------------------------------------------------------------ tipos
    def rule_finding(self, section: str, key: str, object_name: str = "", statement_id: str = "") -> Finding:
        rule = getattr(self, section).get(key, {})
        return Finding(
            severity=Severity(rule.get("severity", "info")),
            rule=f"{section}.{key}",
            message=rule.get("note", key),
            object_name=object_name,
            statement_id=statement_id,
            verified=rule.get("verified"),
            resolved_by="rule",
        )

    def map_type(self, source_type: str, object_name: str = "") -> TypeMapping:
        """Mapea un tipo Hive (incluye anidados) a su tipo Trino/VAST."""
        findings: list[Finding] = []
        target = self._map(source_type.strip(), object_name, findings)
        return TypeMapping(target=target, findings=findings)

    def _map(self, t: str, obj: str, findings: list[Finding]) -> str | None:
        up = t.upper()
        # tipos anidados Hive: ARRAY<..>, MAP<..,..>, STRUCT<a:..,b:..>
        if up.startswith("ARRAY<") and up.endswith(">"):
            inner = self._map(t[6:-1], obj, findings)
            return f"ARRAY({inner})" if inner else None
        if up.startswith("MAP<") and up.endswith(">"):
            k, v = _split_top(t[4:-1])
            mk, mv = self._map(k, obj, findings), self._map(v, obj, findings)
            return f"MAP({mk}, {mv})" if mk and mv else None
        if up.startswith("STRUCT<") and up.endswith(">"):
            fields = []
            for part in _split_top(t[7:-1]):
                name, _, ftype = part.partition(":")
                mt = self._map(ftype, obj, findings)
                if not mt:
                    return None
                fields.append(f"{name.strip()} {mt}")
            return f"ROW({', '.join(fields)})"
        # tipos ya en notacion Trino (ARRAY(...), ROW(...), MAP(...)) que vienen de sqlglot
        if re.match(r"^(ARRAY|MAP|ROW)\(", up):
            return t
        m = _TYPE_RE.match(t)
        if not m:
            findings.append(Finding(Severity.MANUAL, "types.unparsed", f"Tipo no reconocido: {t}", obj))
            return None
        base, args = m.group(1).upper(), m.group(2)
        base = {"TEXT": "STRING", "INT4": "INT", "INT8": "BIGINT"}.get(base, base)
        rule = self.types.get(base)
        if rule is None:
            findings.append(Finding(Severity.MANUAL, "types.unknown", f"Tipo sin regla en la matriz: {t}", obj))
            return None
        sev = Severity(rule.get("severity", "ok"))
        if sev != Severity.OK:
            findings.append(Finding(sev, f"types.{base}", f"{t} -> {rule.get('target')}: {rule.get('note', '')}",
                                    obj, verified=rule.get("verified"), resolved_by="rule"))
        target = rule.get("target")
        if target is None:
            return None
        if base == "DECIMAL":
            p, s = (args or "10,0").split(",") if args and "," in args else ((args or "10"), "0")
            p_i = int(p)
            if p_i > rule.get("max_precision", 38):
                findings.append(Finding(Severity.WARNING, "types.DECIMAL",
                                        f"DECIMAL({p},{s}) excede precision 38; se reduce", obj, verified=True))
                p_i = 38
            return f"DECIMAL({p_i}, {int(s)})"
        return target

    # --------------------------------------------------------------- naming
    def table_name(self, naming: str, catalog: str, bucket: str, schema: str, table: str) -> str:
        pattern = self.naming.get(naming, self.naming.get("plain", "{catalog}.{schema}.{table}"))
        return pattern.format(catalog=catalog, bucket=bucket, schema=schema, table=table)

    def schema_name(self, naming: str, catalog: str, bucket: str, schema: str) -> str:
        if naming == "vast":
            return f'{catalog}."{bucket}/{schema}"'
        return f"{catalog}.{schema}"

    # ------------------------------------------------------------- session
    def session_action(self, sql: str) -> tuple[str, str | None]:
        """Devuelve ('drop'|'manual'|'keep', nota)."""
        for pat in self.session.get("drop_patterns", []):
            if re.search(pat, sql, re.I):
                return "drop", "sentencia de sesion/administracion sin equivalente en VAST"
        for item in self.session.get("manual_patterns", []):
            if re.search(item["pattern"], sql, re.I):
                return "manual", item["note"]
        return "keep", None


def _split_top(s: str) -> list[str]:
    """Divide por comas de primer nivel (respeta <> y ())."""
    parts, depth, cur = [], 0, []
    for ch in s:
        if ch in "<(":
            depth += 1
        elif ch in ">)":
            depth -= 1
        if ch == "," and depth == 0:
            parts.append("".join(cur).strip())
            cur = []
        else:
            cur.append(ch)
    if cur:
        parts.append("".join(cur).strip())
    return parts
