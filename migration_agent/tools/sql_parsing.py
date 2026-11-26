"""Utilidades de parsing SQL Hive/Impala: division de sentencias, clasificacion y DDL -> IR."""
from __future__ import annotations

import logging
import re

import sqlglot
from sqlglot import exp

from ..core.ir import Column, Finding, Severity, Statement, StatementKind, Table

logging.getLogger("sqlglot").setLevel(logging.ERROR)

IMPALA_HINTS = [
    r"\[\s*(SHUFFLE|BROADCAST|NOSHUFFLE|CLUSTERED|NOCLUSTERED)\s*\]",
    r"/\*\s*\+[^*]*\*/",
    r"\bSTRAIGHT_JOIN\b",
]


# ----------------------------------------------------------------- splitting
def split_statements(text: str) -> list[str]:
    """Divide un script en sentencias respetando comillas y comentarios.
    Acepta comentarios '--', '/* */' y lineas que empiezan con '#' (comun en scripts CDP)."""
    out, cur = [], []
    i, n = 0, len(text)
    quote: str | None = None
    while i < n:
        ch = text[i]
        nxt = text[i + 1] if i + 1 < n else ""
        if quote:
            cur.append(ch)
            if ch == "\\" and nxt:
                cur.append(nxt)
                i += 2
                continue
            if ch == quote:
                quote = None
            i += 1
            continue
        if ch in ("'", '"', "`"):
            quote = ch
            cur.append(ch)
        elif ch == "-" and nxt == "-":
            j = text.find("\n", i)
            i = n if j == -1 else j
            continue
        elif ch == "#" and _line_start(text, i):
            j = text.find("\n", i)
            i = n if j == -1 else j
            continue
        elif ch == "/" and nxt == "*" and text[i + 2:i + 3] != "+":
            j = text.find("*/", i + 2)
            i = n if j == -1 else j + 2
            continue
        elif ch == ";":
            stmt = "".join(cur).strip()
            if stmt:
                out.append(stmt)
            cur = []
        else:
            cur.append(ch)
        i += 1
    tail = "".join(cur).strip()
    if tail:
        out.append(tail)
    return out


def _line_start(text: str, i: int) -> bool:
    j = i - 1
    while j >= 0 and text[j] in " \t":
        j -= 1
    return j < 0 or text[j] == "\n"


def strip_impala_hints(sql: str) -> str:
    for pat in IMPALA_HINTS:
        sql = re.sub(pat, " ", sql, flags=re.I)
    return re.sub(r"[ \t]+", " ", sql)


# ------------------------------------------------------------ classification
_OPS = [
    (r"^CREATE\s+(DATABASE|SCHEMA)\b", StatementKind.DDL, "CREATE_DATABASE"),
    (r"^CREATE\s+(OR\s+REPLACE\s+)?MATERIALIZED\s+VIEW\b", StatementKind.DDL, "CREATE_MATERIALIZED_VIEW"),
    (r"^CREATE\s+(OR\s+REPLACE\s+)?VIEW\b", StatementKind.DDL, "CREATE_VIEW"),
    (r"^CREATE\s+(TEMPORARY\s+)?FUNCTION\b", StatementKind.SESSION, "CREATE_FUNCTION"),
    (r"^CREATE\s+(EXTERNAL\s+|TEMPORARY\s+|TRANSACTIONAL\s+)*TABLE\b.*\bAS\s+(SELECT|WITH)\b", StatementKind.DDL, "CTAS"),
    (r"^CREATE\s+(EXTERNAL\s+|TEMPORARY\s+|TRANSACTIONAL\s+)*TABLE\b", StatementKind.DDL, "CREATE_TABLE"),
    (r"^ALTER\s+TABLE\b", StatementKind.DDL, "ALTER_TABLE"),
    (r"^ALTER\s+VIEW\b", StatementKind.DDL, "ALTER_VIEW"),
    (r"^DROP\s+(TABLE|VIEW|DATABASE|SCHEMA)\b", StatementKind.DDL, "DROP"),
    (r"^INSERT\s+OVERWRITE\b", StatementKind.DML, "INSERT_OVERWRITE"),
    (r"^INSERT\s+INTO\b", StatementKind.DML, "INSERT_INTO"),
    (r"^UPSERT\s+INTO\b", StatementKind.DML, "UPSERT"),
    (r"^LOAD\s+DATA\b", StatementKind.DML, "LOAD_DATA"),
    (r"^UPDATE\b", StatementKind.DML, "UPDATE"),
    (r"^DELETE\b", StatementKind.DML, "DELETE"),
    (r"^MERGE\b", StatementKind.DML, "MERGE"),
    (r"^TRUNCATE\b", StatementKind.DML, "TRUNCATE"),
    (r"^(SELECT|WITH|VALUES)\b", StatementKind.DQL, "SELECT"),
    (r"^(SET|RESET|USE|ADD|INVALIDATE|REFRESH|COMPUTE|ANALYZE|MSCK|SHOW|DESCRIBE|DESC|EXPLAIN)\b",
     StatementKind.SESSION, "SESSION"),
]


def classify(sql: str) -> tuple[StatementKind, str]:
    head = re.sub(r"\s+", " ", sql.strip()).upper()
    # "FROM t INSERT ..." (multi-insert Hive)
    if head.startswith("FROM ") and " INSERT " in head:
        return StatementKind.DML, "MULTI_INSERT"
    for pat, kind, op in _OPS:
        if re.match(pat, head, re.S):
            return kind, op
    return StatementKind.UNKNOWN, "UNKNOWN"


def referenced_tables(sql: str) -> list[str]:
    try:
        tree = sqlglot.parse_one(strip_impala_hints(sql), read="hive")
    except Exception:
        return []
    names = []
    for t in tree.find_all(exp.Table):
        if t.name:
            names.append(f"{t.db}.{t.name}" if t.db else t.name)
    return sorted(set(names))


def make_statement(idx: int, sql: str, source_file: str) -> Statement:
    kind, op = classify(sql)
    return Statement(id=f"S{idx:04d}", kind=kind, sql=sql, source_file=source_file,
                     operation=op, tables=referenced_tables(sql) if kind != StatementKind.SESSION else [])


# ------------------------------------------------------------------ DDL -> IR
def table_from_ddl(sql: str, default_db: str = "default", engine: str = "hive") -> tuple[Table | None, list[Finding]]:
    """Construye un Table del IR desde CREATE TABLE / CREATE VIEW Hive/Impala."""
    findings: list[Finding] = []
    clean = strip_impala_hints(sql)
    # Impala: SORT BY (a, b) no lo entiende el parser Hive -> se extrae antes de parsear
    sort_m = re.search(r"\bSORT\s+BY\s*\(([^)]*)\)", clean, re.I)
    if sort_m:
        clean = clean[:sort_m.start()] + clean[sort_m.end():]
    try:
        tree = sqlglot.parse_one(clean, read="hive")
    except Exception as e:  # noqa: BLE001
        findings.append(Finding(Severity.MANUAL, "parse.ddl", f"No se pudo parsear el DDL: {e}"[:300]))
        return None, findings
    if not isinstance(tree, exp.Create):
        return None, findings
    kind = (tree.args.get("kind") or "").upper()
    target = tree.this
    tbl_expr = target.this if isinstance(target, exp.Schema) else target
    if not isinstance(tbl_expr, exp.Table):
        return None, findings
    t = Table(database=(tbl_expr.db or default_db).lower(), name=tbl_expr.name.lower(), source_engine=engine)

    if kind == "VIEW":
        t.is_view = True
        q = tree.expression
        t.view_sql = q.sql(dialect="hive") if q is not None else None
        if isinstance(target, exp.Schema):
            t.columns = [Column(name=c.name.lower(), source_type="") for c in target.expressions]
        return t, findings

    if isinstance(target, exp.Schema):
        for cdef in target.expressions:
            if not isinstance(cdef, exp.ColumnDef):
                continue
            col = Column(name=cdef.name.lower(), source_type=_hive_type(cdef))
            for cons in cdef.args.get("constraints") or []:
                k = cons.args.get("kind")
                if isinstance(k, exp.CommentColumnConstraint):
                    col.comment = k.this.name if hasattr(k.this, "name") else str(k.this)
                elif isinstance(k, exp.NotNullColumnConstraint):
                    col.nullable = False
            t.columns.append(col)

    props = tree.args.get("properties")
    for p in (props.expressions if props else []):
        if isinstance(p, exp.ExternalProperty):
            t.is_external = True
        elif isinstance(p, exp.PartitionedByProperty):
            schema = p.this
            for cdef in getattr(schema, "expressions", []):
                if isinstance(cdef, exp.ColumnDef):
                    t.columns.append(Column(name=cdef.name.lower(), source_type=_hive_type(cdef), is_partition=True))
                    t.partition_columns.append(cdef.name.lower())
                else:  # PARTITIONED BY (col) sin tipo (Iceberg/Impala)
                    t.partition_columns.append(cdef.name.lower())
        elif isinstance(p, exp.ClusteredByProperty):
            t.bucket_columns = [e.name.lower() for e in p.expressions]
            sorted_by = p.args.get("sorted_by") or []
            t.sort_columns = [(e.this.name if isinstance(e, exp.Ordered) else e.name).lower() for e in sorted_by]
            buckets = p.args.get("buckets")
            t.num_buckets = int(buckets.name) if buckets is not None else None
        elif isinstance(p, exp.FileFormatProperty):
            t.file_format = p.this.name if hasattr(p.this, "name") else str(p.this)
        elif isinstance(p, exp.LocationProperty):
            t.location = p.this.name
        elif isinstance(p, exp.SchemaCommentProperty):
            t.comment = p.this.name
        elif isinstance(p, exp.Property):
            t.properties[p.name] = p.text("value") or (p.args["value"].sql() if p.args.get("value") else "")
    # Impala: SORT BY (a, b)
    if sort_m and not t.sort_columns:
        t.sort_columns = [c.strip().strip("`").lower() for c in sort_m.group(1).split(",") if c.strip()]
    return t, findings


def _hive_type(cdef: exp.ColumnDef) -> str:
    k = cdef.args.get("kind")
    return k.sql(dialect="hive") if k is not None else "STRING"
