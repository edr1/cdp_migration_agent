"""FASES 2-3: analisis de compatibilidad y traduccion Hive/Impala -> VAST Database (SQL Trino).

Estrategia "deterministico primero":
  1. Reglas de la matriz de equivalencia + sqlglot (hive -> trino) resuelven la mayoria.
  2. Lo que queda como severity=manual se marca para revision (LLM via NIM y/o humano).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

import sqlglot
from sqlglot import exp

from ..core.config import AgentConfig
from ..core.ir import Finding, Inventory, Severity, Statement, StatementKind, Table
from ..core.matrix import EquivalenceMatrix
from .sql_parsing import strip_impala_hints


@dataclass
class TargetNaming:
    naming: str           # vast | plain
    catalog: str
    bucket: str
    schema_map: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_config(cls, cfg: AgentConfig) -> "TargetNaming":
        return cls(naming=cfg.get("target.naming", "vast"), catalog=cfg.get("target.trino.catalog", "vast"),
                   bucket=cfg.get("target.bucket", "cdp-migration"), schema_map=cfg.get("target.schema_map") or {})

    def schema(self, db: str) -> str:
        return self.schema_map.get(db, db)

    def schema_ref(self, db: str) -> str:
        s = self.schema(db)
        return f'{self.catalog}."{self.bucket}/{s}"' if self.naming == "vast" else f"{self.catalog}.{s}"

    def table_ref(self, db: str, table: str) -> str:
        return f"{self.schema_ref(db)}.{table}"

    def table_expr(self, db: str, table: str) -> exp.Table:
        s = self.schema(db)
        if self.naming == "vast":
            return exp.Table(this=exp.to_identifier(table), db=exp.to_identifier(f"{self.bucket}/{s}", quoted=True),
                             catalog=exp.to_identifier(self.catalog))
        return exp.Table(this=exp.to_identifier(table), db=exp.to_identifier(s), catalog=exp.to_identifier(self.catalog))


class Translator:
    def __init__(self, matrix: EquivalenceMatrix, naming: TargetNaming, inventory: Inventory,
                 apply_sorted_by: bool = True):
        self.m = matrix
        self.n = naming
        self.inv = inventory
        self.apply_sorted_by = apply_sorted_by

    # ================================================================ DDL
    def create_schemas(self) -> list[str]:
        return [f"CREATE SCHEMA IF NOT EXISTS {self.n.schema_ref(db)}" for db in self.inv.databases]

    def translate_table(self, t: Table) -> tuple[list[str], list[Finding]]:
        if t.is_view:
            return self._translate_view(t)
        f: list[Finding] = []
        name = t.fqn
        cols_sql = []
        for c in t.columns:
            tm = self.m.map_type(c.source_type, f"{name}.{c.name}")
            f.extend(tm.findings)
            if tm.target is None:
                c.target_type = None
                cols_sql.append(f"  -- MANUAL: {c.name} {c.source_type} sin equivalente\n  {_q(c.name)} VARCHAR")
                f.append(Finding(Severity.MANUAL, "types.no_target",
                                 f"{c.name} {c.source_type}: se propone VARCHAR (serializado) para revision", name))
                continue
            c.target_type = tm.target
            cols_sql.append(f"  {_q(c.name)} {tm.target}")
            if not c.nullable:
                f.append(self.m.rule_finding("ddl", "constraints", f"{name}.{c.name}"))
        if t.file_format:
            f.append(self.m.rule_finding("ddl", "stored_as", name))
        if t.location:
            f.append(self.m.rule_finding("ddl", "location", name))
        if t.properties:
            f.append(self.m.rule_finding("ddl", "tblproperties", name))
        if t.is_external:
            f.append(self.m.rule_finding("ddl", "external", name))

        with_props = []
        part_exprs = [f"'{p}'" for p in t.partition_columns]
        if t.bucket_columns and t.num_buckets:
            part_exprs += [f"'bucket({c}, {t.num_buckets})'" for c in t.bucket_columns]
            f.append(self.m.rule_finding("ddl", "clustered_by", name))
        if part_exprs:
            f.append(self.m.rule_finding("ddl", "partitioned_by", name))
            max_keys = self.m.ddl.get("partitioned_by", {}).get("max_keys", 4)
            if len(part_exprs) > max_keys:
                f.append(Finding(Severity.MANUAL, "ddl.partition_keys",
                                 f"{len(part_exprs)} llaves de particion > maximo VAST ({max_keys}); elegir cuales conservar", name))
                part_exprs = part_exprs[:max_keys]
            with_props.append(f"partitioning = ARRAY[{', '.join(part_exprs)}]")
        post = []
        if t.sort_columns:
            f.append(self.m.rule_finding("ddl", "sorted_by", name))
            # una columna con particion identity no puede ser columna de orden (doc VAST)
            sort = [c for c in t.sort_columns if c not in t.partition_columns][: self.m.ddl.get("sorted_by", {}).get("max_columns", 4)]
            if sort and self.apply_sorted_by:
                arr = "ARRAY[" + ", ".join(f"'{c}'" for c in sort) + "]"
                if part_exprs:
                    # VAST: sorted + partitioned solo se permite al crear la tabla
                    with_props.append(f"sorted_by = {arr}")
                    f.append(Finding(Severity.WARNING, "ddl.sorted_partitioned",
                                     "Tabla particionada y ordenada: sorted_by va en CREATE TABLE ... WITH "
                                     "(sintaxis dentro de WITH no confirmada en la doc VAST; validar)", name, verified=False))
                else:
                    post.append(f"ALTER TABLE {self.n.table_ref(t.database, t.name)} SET PROPERTIES sorted_by = {arr}")
        ddl = f"CREATE TABLE IF NOT EXISTS {self.n.table_ref(t.database, t.name)} (\n" + ",\n".join(cols_sql) + "\n)"
        if with_props:
            ddl += "\nWITH (" + ", ".join(with_props) + ")"
        return [ddl] + post, f

    def _translate_view(self, t: Table) -> tuple[list[str], list[Finding]]:
        f = [self.m.rule_finding("ddl", "view", t.fqn)]
        if not t.view_sql:
            f.append(Finding(Severity.MANUAL, "ddl.view_body", "Vista sin cuerpo SQL", t.fqn))
            return [], f
        body, ff = self.translate_query(t.view_sql, t.database, t.fqn)
        f.extend(ff)
        return [f"CREATE OR REPLACE VIEW {self.n.table_ref(t.database, t.name)} AS\n{body}"], f

    # ================================================================ DQL / DML
    def translate_query(self, sql: str, default_db: str = "default", obj: str = "") -> tuple[str, list[Finding]]:
        f: list[Finding] = []
        clean = strip_impala_hints(sql)
        if clean != sql:
            f.append(Finding(Severity.INFO, "dql.impala_hints", "Hints de Impala eliminados", obj))
        tree = sqlglot.parse_one(clean, read="hive")
        self._check_functions(tree, f, obj)
        tree = self._fix_implicit_casts(tree, default_db, f, obj)
        tree = self._rename_tables(tree, default_db)
        return tree.sql(dialect="trino", pretty=True), f

    def _column_types(self, tree: exp.Expression, default_db: str) -> dict[str, str]:
        types: dict[str, str] = {}
        for t in tree.find_all(exp.Table):
            tbl = self.inv.get_table(f"{(t.db or default_db)}.{t.name}")
            if tbl is None:
                continue
            for c in tbl.columns:
                tt = c.target_type or self.m.map_type(c.source_type).target or ""
                types.setdefault(c.name.lower(), tt.upper())
        return types

    def _fix_implicit_casts(self, tree: exp.Expression, default_db: str, f: list[Finding], obj: str) -> exp.Expression:
        """Hive convierte implicitamente string<->date/timestamp/numero; Trino no.
        Reescribe literales comparados contra columnas tipadas: fecha = '2026-01-01' -> DATE '2026-01-01'."""
        types = self._column_types(tree, default_db)
        if not types:
            return tree
        fixed = []

        def lit_for(col: exp.Expression, lit: exp.Expression) -> exp.Expression:
            if not (isinstance(col, exp.Column) and isinstance(lit, exp.Literal)):
                return lit
            t = types.get(col.name.lower(), "")
            if lit.is_string and t.startswith(("DATE", "TIMESTAMP")) and re.match(r"^\d{4}-\d{2}-\d{2}", lit.this):
                fixed.append(f"{col.name} {t.split('(')[0]}")
                kind = "DATE" if t.startswith("DATE") else "TIMESTAMP"
                return exp.cast(lit, kind)
            if lit.is_string and t.startswith(("INTEGER", "BIGINT", "SMALLINT", "TINYINT", "DOUBLE", "DECIMAL")) \
                    and re.match(r"^-?\d+(\.\d+)?$", lit.this):
                fixed.append(f"{col.name} numerico")
                return exp.Literal.number(lit.this)
            if lit.is_number and t.startswith("VARCHAR"):
                fixed.append(f"{col.name} VARCHAR")
                return exp.Literal.string(lit.this)
            return lit

        def _fix(node):
            if isinstance(node, (exp.EQ, exp.NEQ, exp.GT, exp.GTE, exp.LT, exp.LTE)):
                l, r = node.left, node.right
                node.set("expression", lit_for(l, r))
                node.set("this", lit_for(r, l))
            elif isinstance(node, exp.Between):
                node.set("low", lit_for(node.this, node.args["low"]))
                node.set("high", lit_for(node.this, node.args["high"]))
            elif isinstance(node, exp.In) and node.args.get("expressions"):
                node.set("expressions", [lit_for(node.this, e) for e in node.expressions])
            return node
        tree = tree.transform(_fix)
        if fixed:
            f.append(Finding(Severity.INFO, "dql.implicit_cast",
                             "Conversiones implicitas de Hive hechas explicitas para Trino: " +
                             ", ".join(sorted(set(fixed))), obj, resolved_by="rule"))
        return tree

    def _check_functions(self, tree: exp.Expression, f: list[Finding], obj: str) -> None:
        names = {(fn.sql_name() if not isinstance(fn, exp.Anonymous) else fn.name).upper()
                 for fn in tree.find_all(exp.Func)}
        for item in self.m.dql.get("review_functions", []):
            if item["name"].upper() in names:
                f.append(Finding(Severity.WARNING, f"dql.function.{item['name'].lower()}", item["note"], obj))
        for item in self.m.dql.get("unsupported_functions", []):
            if item["name"].upper() in names or item["name"].upper() in tree.sql().upper():
                f.append(Finding(Severity.MANUAL, f"dql.unsupported.{item['name'].lower()}", item["note"], obj))

    def _rename_tables(self, tree: exp.Expression, default_db: str) -> exp.Expression:
        cte_names = {c.alias_or_name.lower() for c in tree.find_all(exp.CTE)}

        def _swap(node):
            if isinstance(node, exp.Table) and node.name and not node.args.get("catalog"):
                if node.name.lower() in cte_names and not node.db:
                    return node
                db = (node.db or default_db).lower()
                new = self.n.table_expr(db, node.name.lower())
                if node.args.get("alias"):
                    new.set("alias", node.args["alias"])
                return new
            return node
        return tree.transform(_swap)

    def translate_statement(self, st: Statement, default_db: str = "default") -> None:
        """Traduce una sentencia DML/DQL/SESSION in-place (translated_sql, status, findings)."""
        if st.kind == StatementKind.SESSION or st.operation == "CREATE_FUNCTION":
            action, note = self.m.session_action(st.sql)
            if action == "manual" or st.operation == "CREATE_FUNCTION":
                st.status = "manual"
                st.findings.append(Finding(Severity.MANUAL, "session.manual", note or "UDF", statement_id=st.id))
            else:
                st.status = "dropped"
                st.findings.append(Finding(Severity.INFO, "session.drop", note or "sin equivalente", statement_id=st.id))
            return
        try:
            handler = {
                "INSERT_OVERWRITE": self._insert_overwrite,
                "INSERT_INTO": self._insert_into,
                "LOAD_DATA": self._load_data,
                "TRUNCATE": self._truncate,
                "UPSERT": self._manual("dml", "upsert"),
                "MULTI_INSERT": self._manual_msg("Multi-insert de Hive (FROM ... INSERT ...): dividir en INSERT individuales"),
                "CREATE_MATERIALIZED_VIEW": self._manual("ddl", "materialized_view"),
                "CTAS": self._ctas,
            }.get(st.operation, self._generic)
            handler(st, default_db)
        except Exception as e:  # noqa: BLE001
            st.status = "manual"
            st.findings.append(Finding(Severity.MANUAL, "translate.error", f"No se pudo traducir: {e}"[:400],
                                       statement_id=st.id))

    # --- handlers -------------------------------------------------------
    def _generic(self, st: Statement, db: str) -> None:
        sql, f = self.translate_query(st.sql, db, st.id)
        st.translated_sql = [sql]
        for x in f:
            x.statement_id = st.id
        st.findings.extend(f)
        if st.operation in ("UPDATE", "MERGE", "DELETE"):
            key = st.operation.lower()
            st.findings.append(self.m.rule_finding("dml", key, statement_id=st.id))
        st.status = "manual" if any(x.severity == Severity.MANUAL for x in f) else "translated"

    def _insert_into(self, st: Statement, db: str) -> None:
        if re.search(r"\bPARTITION\s*\(", st.sql, re.I):
            self._insert_partitioned(st, db, overwrite=False)
        else:
            self._generic(st, db)

    def _insert_overwrite(self, st: Statement, db: str) -> None:
        self._insert_partitioned(st, db, overwrite=True)

    def _insert_partitioned(self, st: Statement, db: str, overwrite: bool) -> None:
        """INSERT [OVERWRITE] TABLE t [PARTITION (p='v', q)] SELECT ...
        -> [START TRANSACTION; DELETE ...;] INSERT INTO t (cols) SELECT ..., 'v' [; COMMIT]"""
        clean = strip_impala_hints(st.sql)
        m = re.match(r"^\s*INSERT\s+(?:OVERWRITE|INTO)\s+(?:TABLE\s+)?([`\w.]+)\s*(PARTITION\s*\((.*?)\))?\s*(.*)$",
                     clean, re.I | re.S)
        if not m:
            return self._manual_msg("INSERT no reconocido")(st, db)
        tname, part_clause, body = m.group(1).replace("`", ""), m.group(3), m.group(4)
        tdb, tbl = (tname.split(".", 1) if "." in tname else (db, tname))
        target = self.inv.get_table(f"{tdb}.{tbl}")
        static: dict[str, str] = {}
        if part_clause:
            for part in part_clause.split(","):
                k, _, v = part.partition("=")
                if v.strip():
                    static[k.strip().lower()] = v.strip()
            st.findings.append(self.m.rule_finding("dml", "insert_partition_static", statement_id=st.id))
        select_sql, f = self.translate_query(body, db, st.id)
        for x in f:
            x.statement_id = st.id
        st.findings.extend(f)
        if static:
            sel = sqlglot.parse_one(select_sql, read="trino")
            for v in static.values():
                sel = sel.select(sqlglot.parse_one(v, read="hive"), append=True) if isinstance(sel, exp.Select) else sel
            select_sql = sel.sql(dialect="trino", pretty=True)
        cols = ""
        if target is not None:
            data_cols = [c.name for c in target.columns if c.name not in static]
            data_cols += [c for c in static]  # literales al final, en el orden del PARTITION()
            cols = " (" + ", ".join(_q(c) for c in data_cols) + ")"
        elif static:
            st.findings.append(Finding(Severity.MANUAL, "dml.unknown_target",
                                       f"Tabla destino {tdb}.{tbl} no esta en el inventario: validar orden de columnas",
                                       statement_id=st.id))
        ref = self.n.table_ref(tdb.lower(), tbl.lower())
        stmts = []
        if overwrite:
            st.findings.append(self.m.rule_finding("dml", "insert_overwrite", statement_id=st.id))
            where = " AND ".join(f"{_q(k)} = {sqlglot.transpile(v, read='hive', write='trino')[0]}" for k, v in static.items())
            stmts += ["START TRANSACTION", f"DELETE FROM {ref}" + (f" WHERE {where}" if where else "")]
        stmts.append(f"INSERT INTO {ref}{cols}\n{select_sql}")
        if overwrite:
            stmts.append("COMMIT")
        st.translated_sql = stmts
        st.status = "manual" if any(x.severity == Severity.MANUAL for x in st.findings) else "translated"

    def _ctas(self, st: Statement, db: str) -> None:
        st.findings.append(self.m.rule_finding("ddl", "ctas", statement_id=st.id))
        m = re.match(r"^\s*CREATE\s+(?:EXTERNAL\s+|TEMPORARY\s+)*TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?([`\w.]+)(.*?)\bAS\s+(SELECT|WITH)\b(.*)$",
                     strip_impala_hints(st.sql), re.I | re.S)
        if not m:
            return self._generic(st, db)
        tname = m.group(1).replace("`", "")
        tdb, tbl = (tname.split(".", 1) if "." in tname else (db, tname))
        if m.group(2).strip():
            st.findings.append(self.m.rule_finding("ddl", "stored_as", statement_id=st.id))
        q, f = self.translate_query(m.group(3) + m.group(4), db, st.id)
        st.findings.extend(f)
        st.translated_sql = [f"CREATE TABLE {self.n.table_ref(tdb.lower(), tbl.lower())} AS\n{q}"]
        st.status = "translated"

    def _load_data(self, st: Statement, db: str) -> None:
        st.findings.append(self.m.rule_finding("dml", "load_data", statement_id=st.id))
        m = re.search(r"INPATH\s+'([^']+)'.*?INTO\s+TABLE\s+([`\w.]+)", st.sql, re.I | re.S)
        if m:
            tname = m.group(2).replace("`", "")
            tdb, tbl = (tname.split(".", 1) if "." in tname else (db, tname))
            st.translated_sql = [
                f"-- MANUAL: cargar los archivos de {m.group(1)} en {self.n.table_ref(tdb, tbl)}\n"
                f"-- Opcion A (federado): INSERT INTO {self.n.table_ref(tdb, tbl)} SELECT * FROM hive.{tdb}.{tbl}\n"
                f"-- Opcion B: copiar los Parquet al bucket '{self.n.bucket}' e importarlos con vast.import_data"]
        st.status = "manual"

    def _truncate(self, st: Statement, db: str) -> None:
        st.findings.append(self.m.rule_finding("dml", "truncate", statement_id=st.id))
        m = re.search(r"TRUNCATE\s+(?:TABLE\s+)?([`\w.]+)", st.sql, re.I)
        tname = m.group(1).replace("`", "") if m else ""
        tdb, tbl = (tname.split(".", 1) if "." in tname else (db, tname))
        st.translated_sql = [f"DELETE FROM {self.n.table_ref(tdb.lower(), tbl.lower())}"]
        st.status = "translated"

    def _manual(self, section: str, key: str):
        def h(st: Statement, db: str) -> None:
            st.findings.append(self.m.rule_finding(section, key, statement_id=st.id))
            st.status = "manual"
        return h

    def _manual_msg(self, msg: str):
        def h(st: Statement, db: str) -> None:
            st.findings.append(Finding(Severity.MANUAL, "translate.manual", msg, statement_id=st.id))
            st.status = "manual"
        return h


def _q(name: str) -> str:
    return f'"{name}"' if not re.match(r"^[a-z_][a-z0-9_]*$", name) else name
