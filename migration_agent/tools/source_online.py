"""FASE 1 (online): descubrimiento directo contra Cloudera CDP (HiveServer2 / Impala / Ranger REST).

El descubrimiento online obtiene el mismo texto que se entregaria en modo offline
(SHOW CREATE TABLE) y lo pasa por el mismo parser, garantizando un unico camino de traduccion.
"""
from __future__ import annotations

import json
import os
import urllib.request
from base64 import b64encode

from ..core.config import AgentConfig
from ..core.ir import Finding, Inventory, Severity
from .source_offline import parse_ranger_export
from .sql_parsing import make_statement, table_from_ddl


class HiveSource:
    def __init__(self, cfg: AgentConfig):
        self.cfg = cfg
        h = cfg.get("source.hive", {})
        try:
            from pyhive import hive
        except ImportError as e:  # pragma: no cover
            raise RuntimeError("Instala 'pyhive[hive_pure_sasl]' para el modo origen online") from e
        self.conn = hive.connect(host=h.get("host"), port=int(h.get("port", 10000)),
                                 username=h.get("username", "hive"), auth=h.get("auth", "NONE"))

    def query(self, sql: str) -> list[tuple]:
        cur = self.conn.cursor()
        cur.execute(sql)
        try:
            return cur.fetchall()
        finally:
            cur.close()


def discover(cfg: AgentConfig, with_counts: bool = True) -> tuple[Inventory, list[Finding]]:
    src = HiveSource(cfg)
    inv = Inventory(origin="online")
    findings: list[Finding] = []
    dbs = cfg.get("source.hive.databases") or [r[0] for r in src.query("SHOW DATABASES")]
    idx = 0
    for db in dbs:
        inv.databases.append(db)
        for (tbl, *_) in src.query(f"SHOW TABLES IN {db}"):
            try:
                ddl = "\n".join(r[0] for r in src.query(f"SHOW CREATE TABLE {db}.{tbl}"))
            except Exception as e:  # noqa: BLE001
                findings.append(Finding(Severity.WARNING, "discover.ddl", f"{db}.{tbl}: {e}"[:300], f"{db}.{tbl}"))
                continue
            idx += 1
            st = make_statement(idx, ddl, f"hive://{db}.{tbl}")
            inv.statements.append(st)
            table, fnd = table_from_ddl(ddl, db, "hive")
            st.findings.extend(fnd)
            if table:
                if with_counts and not table.is_view:
                    try:
                        table.row_count = int(src.query(f"SELECT COUNT(*) FROM {db}.{tbl}")[0][0])
                    except Exception as e:  # noqa: BLE001
                        findings.append(Finding(Severity.INFO, "discover.count", str(e)[:200], table.fqn))
                inv.add_table(table)
    inv.policies.extend(fetch_ranger_policies(cfg, findings))
    return inv, findings


def fetch_ranger_policies(cfg: AgentConfig, findings: list[Finding]) -> list:
    url = cfg.get("source.ranger.url")
    if not url:
        return []
    user = cfg.get("source.ranger.username") or ""
    pwd = os.environ.get(cfg.get("source.ranger.password_env", "RANGER_PASSWORD"), "")
    out = []
    for svc in cfg.get("source.ranger.services", []):
        req = urllib.request.Request(f"{url.rstrip('/')}/service/public/v2/api/service/{svc}/policy")
        if user:
            req.add_header("Authorization", "Basic " + b64encode(f"{user}:{pwd}".encode()).decode())
        try:
            with urllib.request.urlopen(req, timeout=20) as r:  # noqa: S310 (URL de configuracion)
                out.extend(parse_ranger_export(json.loads(r.read())))
        except Exception as e:  # noqa: BLE001
            findings.append(Finding(Severity.WARNING, "ranger.fetch", f"{svc}: {e}"[:300]))
    return out
