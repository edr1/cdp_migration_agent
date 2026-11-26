"""Registro de tools expuestas al LLM (tool calling formato OpenAI) para el modo agente interactivo
y para los nodos LLM del pipeline. Cada tool es una funcion Python deterministica."""
from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any, Callable

import sqlglot

from ..core.ir import StatementKind
from ..tools.sql_parsing import classify, make_statement


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    fn: Callable[..., Any]
    side_effects: bool = False   # True => requiere destino online y confirmacion

    def spec(self) -> dict:
        return {"type": "function", "function": {"name": self.name, "description": self.description,
                                                 "parameters": self.parameters}}


class ToolRegistry:
    def __init__(self, ctx):
        """ctx: RunContext del pipeline (inventory, matrix, translator, skills, target, ...)."""
        self.ctx = ctx
        self.tools: dict[str, Tool] = {}
        self._register_defaults()

    def add(self, tool: Tool) -> None:
        self.tools[tool.name] = tool

    def specs(self, allow_side_effects: bool = False) -> list[dict]:
        return [t.spec() for t in self.tools.values() if allow_side_effects or not t.side_effects]

    def call(self, name: str, arguments: str | dict) -> str:
        tool = self.tools.get(name)
        if tool is None:
            return json.dumps({"error": f"tool desconocida: {name}"})
        args = json.loads(arguments) if isinstance(arguments, str) and arguments else (arguments or {})
        try:
            return json.dumps(tool.fn(**args), ensure_ascii=False, default=str)[:12000]
        except Exception as e:  # noqa: BLE001
            return json.dumps({"error": str(e)[:500]})

    # ------------------------------------------------------------------
    def _register_defaults(self) -> None:
        c = self.ctx
        S = lambda props, req=None: {"type": "object", "properties": props, "required": req or []}  # noqa: E731

        self.add(Tool("list_tables", "Lista las tablas/vistas del inventario de origen con su numero de columnas.",
                      S({}), lambda: [{"table": t.fqn, "view": t.is_view, "columns": len(t.columns),
                                       "partitions": t.partition_columns} for t in c.inventory.tables.values()]))
        self.add(Tool("describe_table", "Devuelve columnas, tipos origen y tipos VAST de una tabla (db.tabla).",
                      S({"table": {"type": "string"}}, ["table"]),
                      lambda table: _describe(c, table)))
        self.add(Tool("lookup_matrix", "Consulta la matriz de equivalencia Hive/Impala->VAST para un tipo, "
                      "clausula DDL, operacion DML o tema (types|ddl|dml|dql|security).",
                      S({"section": {"type": "string"}, "key": {"type": "string"}}, ["section"]),
                      lambda section, key=None: _matrix(c, section, key)))
        self.add(Tool("translate_sql", "Traduce una sentencia Hive/Impala a SQL Trino para VAST usando las reglas "
                      "deterministicas. Devuelve sentencias traducidas y hallazgos.",
                      S({"sql": {"type": "string"}, "default_db": {"type": "string"}}, ["sql"]),
                      lambda sql, default_db="default": _translate(c, sql, default_db)))
        self.add(Tool("validate_trino_syntax", "Verifica que un SQL sea sintacticamente valido en dialecto Trino.",
                      S({"sql": {"type": "string"}}, ["sql"]), _validate_syntax))
        self.add(Tool("read_skill", "Lee el SKILL.md o una referencia de una skill del motor.",
                      S({"skill": {"type": "string"}, "reference": {"type": "string"}}, ["skill"]),
                      lambda skill, reference=None: _read_skill(c, skill, reference)))
        self.add(Tool("synthetic_preview", "Genera N filas sinteticas de ejemplo para una tabla (no escribe a disco).",
                      S({"table": {"type": "string"}, "rows": {"type": "integer"}}, ["table"]),
                      lambda table, rows=5: _synth_preview(c, table, rows)))
        self.add(Tool("run_trino_query", "Ejecuta una consulta SOLO LECTURA (SELECT/SHOW/DESCRIBE) en VAST via Trino.",
                      S({"sql": {"type": "string"}}, ["sql"]), lambda sql: _run_readonly(c, sql), side_effects=True))


def _describe(c, table: str) -> dict:
    t = c.inventory.get_table(table)
    if t is None:
        return {"error": f"{table} no existe en el inventario"}
    return {"table": t.fqn, "view": t.is_view, "partition_columns": t.partition_columns,
            "sort_columns": t.sort_columns, "columns": [
                {"name": x.name, "source_type": x.source_type, "target_type": x.target_type,
                 "semantic": x.semantic} for x in t.columns]}


def _matrix(c, section: str, key: str | None) -> Any:
    data = c.matrix.data.get(section, {})
    if key and isinstance(data, dict):
        return data.get(key) or data.get(key.upper()) or {"error": f"sin regla {section}.{key}"}
    return data


def _translate(c, sql: str, default_db: str) -> dict:
    st = make_statement(0, sql, "agent")
    if st.kind == StatementKind.DDL and st.operation in ("CREATE_TABLE", "CREATE_VIEW"):
        from ..tools.sql_parsing import table_from_ddl
        t, f = table_from_ddl(sql, default_db)
        if t is None:
            return {"error": "DDL no parseable", "findings": [x.to_dict() for x in f]}
        out, f2 = c.translator.translate_table(t)
        return {"sql": out, "findings": [x.to_dict() for x in f + f2]}
    c.translator.translate_statement(st, default_db)
    return {"operation": classify(sql)[1], "status": st.status, "sql": st.translated_sql,
            "findings": [x.to_dict() for x in st.findings]}


def _validate_syntax(sql: str) -> dict:
    try:
        for s in sqlglot.parse(sql, read="trino"):
            if s is None:
                continue
        return {"valid": True}
    except Exception as e:  # noqa: BLE001
        return {"valid": False, "error": str(e)[:400]}


def _read_skill(c, skill: str, reference: str | None) -> dict:
    sk = c.skills.get(skill)
    if sk is None:
        return {"error": f"skill desconocida; disponibles: {list(c.skills.skills)}"}
    if reference:
        return {"skill": skill, "reference": reference, "content": sk.reference(reference)}
    return {"skill": skill, "content": sk.body, "references": sk.references()}


def _synth_preview(c, table: str, rows: int) -> dict:
    from ..tools.synth import SyntheticGenerator
    t = c.inventory.get_table(table)
    if t is None:
        return {"error": f"{table} no existe"}
    gen = SyntheticGenerator(c.inventory, c.synth_cfg)
    gen.data = {}
    g = {col.name: gen._column_generator(t, col) for col in t.columns}  # noqa: SLF001
    return {"table": table, "rows": [{k: fn(i) for k, fn in g.items()} for i in range(min(rows, 20))]}


def _run_readonly(c, sql: str) -> dict:
    if c.target is None:
        return {"error": "destino VAST offline: no se pueden ejecutar consultas"}
    head = sql.strip().split(None, 1)[0].upper() if sql.strip() else ""
    if head not in ("SELECT", "SHOW", "DESCRIBE", "EXPLAIN", "WITH"):
        return {"error": "solo se permiten consultas de lectura"}
    r = c.target.execute(sql)
    return {"ok": r.ok, "rows": r.rows[:50], "error": r.error}
