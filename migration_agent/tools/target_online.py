"""FASE 5 (online): despliegue y validacion en VAST Database a traves del cliente Trino."""
from __future__ import annotations

from dataclasses import dataclass, field

from ..core.config import AgentConfig


@dataclass
class ExecResult:
    sql: str
    ok: bool
    error: str | None = None
    rows: list = field(default_factory=list)


class TrinoTarget:
    def __init__(self, cfg: AgentConfig):
        try:
            import trino
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("Instala el paquete 'trino' para el modo destino online") from e
        t = cfg.get("target.trino", {})
        self.catalog = t.get("catalog", "vast")
        self.conn = trino.dbapi.connect(host=t.get("host"), port=int(t.get("port", 8080)), user=t.get("user"),
                                        http_scheme=t.get("http_scheme", "http"), catalog=self.catalog)

    def query(self, sql: str) -> list[tuple]:
        cur = self.conn.cursor()
        cur.execute(sql.rstrip().rstrip(";"))
        return cur.fetchall()

    def execute(self, sql: str) -> ExecResult:
        try:
            return ExecResult(sql, True, rows=self.query(sql))
        except Exception as e:  # noqa: BLE001
            return ExecResult(sql, False, error=str(e)[:1000])

    def execute_script(self, statements: list[str], stop_on_error: bool = False) -> list[ExecResult]:
        results = []
        for s in statements:
            if not s.strip() or s.lstrip().startswith("--") and "\n" not in s.strip():
                continue
            r = self.execute(s)
            results.append(r)
            if not r.ok and stop_on_error:
                break
        return results
