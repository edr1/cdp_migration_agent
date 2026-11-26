"""Representacion intermedia (IR) canonica del motor de migracion.

Todo lo que entra (DDL/DML/DQL/politicas Ranger, online u offline) se normaliza a este
modelo; todo lo que sale (scripts VAST, datos sinteticos, validaciones) se genera desde aqui.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class StatementKind(str, Enum):
    DDL = "DDL"
    DML = "DML"
    DQL = "DQL"
    SESSION = "SESSION"
    UNKNOWN = "UNKNOWN"


class Severity(str, Enum):
    OK = "ok"
    INFO = "info"
    WARNING = "warning"
    MANUAL = "manual"
    ERROR = "error"


@dataclass
class Finding:
    """Hallazgo de compatibilidad / traduccion (alimenta el reporte y la revision humana/LLM)."""
    severity: Severity
    rule: str
    message: str
    object_name: str = ""
    statement_id: str = ""
    verified: bool | None = None
    suggestion: str | None = None
    resolved_by: str | None = None  # "rule" | "llm" | "human"

    def to_dict(self) -> dict:
        d = asdict(self)
        d["severity"] = self.severity.value
        return d


@dataclass
class Column:
    name: str
    source_type: str              # tipo tal cual en el origen (ej. "STRING", "DECIMAL(10,2)")
    target_type: str | None = None  # tipo VAST/Trino resuelto por la matriz
    comment: str | None = None
    nullable: bool = True
    is_partition: bool = False
    semantic: str | None = None   # pista para datos sinteticos (email, person_name, ...)


@dataclass
class Table:
    database: str
    name: str
    columns: list[Column] = field(default_factory=list)
    partition_columns: list[str] = field(default_factory=list)
    bucket_columns: list[str] = field(default_factory=list)
    num_buckets: int | None = None
    sort_columns: list[str] = field(default_factory=list)
    is_external: bool = False
    is_view: bool = False
    view_sql: str | None = None   # SELECT de la vista (dialecto origen)
    file_format: str | None = None
    location: str | None = None
    comment: str | None = None
    properties: dict[str, str] = field(default_factory=dict)
    source_engine: str = "hive"   # hive | impala
    row_count: int | None = None  # conocido en modo online

    @property
    def fqn(self) -> str:
        return f"{self.database}.{self.name}"

    def column(self, name: str) -> Column | None:
        return next((c for c in self.columns if c.name.lower() == name.lower()), None)


@dataclass
class Statement:
    id: str
    kind: StatementKind
    sql: str
    source_file: str = ""
    operation: str = ""           # CREATE_TABLE, INSERT_OVERWRITE, SELECT, ...
    tables: list[str] = field(default_factory=list)
    translated_sql: list[str] = field(default_factory=list)
    status: str = "pending"       # pending | translated | dropped | manual | failed
    findings: list[Finding] = field(default_factory=list)


@dataclass
class RangerPolicy:
    id: str
    name: str
    service: str
    policy_type: str              # access | masking | rowfilter
    databases: list[str] = field(default_factory=list)
    tables: list[str] = field(default_factory=list)
    columns: list[str] = field(default_factory=list)
    items: list[dict[str, Any]] = field(default_factory=list)  # usuarios/grupos/accesos/mascaras/filtros
    is_enabled: bool = True
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass
class Inventory:
    """Inventario completo del origen."""
    databases: list[str] = field(default_factory=list)
    tables: dict[str, Table] = field(default_factory=dict)     # fqn -> Table
    statements: list[Statement] = field(default_factory=list)
    policies: list[RangerPolicy] = field(default_factory=list)
    origin: str = "offline"                                    # online | offline

    def add_table(self, t: Table) -> None:
        self.tables[t.fqn.lower()] = t
        if t.database not in self.databases:
            self.databases.append(t.database)

    def get_table(self, fqn: str) -> Table | None:
        return self.tables.get(fqn.lower())

    def summary(self) -> dict:
        kinds: dict[str, int] = {}
        for s in self.statements:
            kinds[s.kind.value] = kinds.get(s.kind.value, 0) + 1
        return {
            "origin": self.origin,
            "databases": self.databases,
            "tables": sum(1 for t in self.tables.values() if not t.is_view),
            "views": sum(1 for t in self.tables.values() if t.is_view),
            "statements": kinds,
            "ranger_policies": len(self.policies),
        }
