"""Orquestador del motor agentico: maquina de estados por fases con enrutamiento por conectividad.

    P0 probe ─▶ P1 discover ─▶ P2 analyze/translate ─▶ P3 security ─▶ P4 synthetic ─▶ P5 deploy+validate ─▶ P6 package
                (online|offline)                                                     (online|offline)

Cada fase: (1) carga su SKILL.md, (2) ejecuta tools deterministicas, (3) opcionalmente invoca al LLM
(NVIDIA NIM) para lo que las reglas no resuelven, (4) registra eventos y hallazgos en el estado.
"""
from __future__ import annotations

import csv
import json
import re
import time
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from ..core.config import AgentConfig
from ..core.ir import Finding, Inventory, Severity, StatementKind, Table
from ..core.llm import NIMClient, extract_json
from ..core.matrix import EquivalenceMatrix
from ..core.skills import SkillRegistry
from ..tools import connectivity, ranger, source_offline, validate
from ..tools.synth import SynthConfig, SyntheticGenerator, render_inserts, write_outputs
from ..tools.translate import TargetNaming, Translator

PHASES = ["P0_probe", "P1_discover", "P2_translate", "P3_security", "P4_synthetic", "P5_validate", "P6_package"]


@dataclass
class RunContext:
    cfg: AgentConfig
    run_dir: Path
    matrix: EquivalenceMatrix
    skills: SkillRegistry
    naming: TargetNaming
    synth_cfg: SynthConfig
    inventory: Inventory = field(default_factory=Inventory)
    translator: Translator | None = None
    modes: dict = field(default_factory=dict)
    llm: NIMClient | None = None
    target: object | None = None
    findings: list[Finding] = field(default_factory=list)
    events: list[dict] = field(default_factory=list)
    artifacts: dict[str, str] = field(default_factory=dict)
    ddl_scripts: dict[str, list[str]] = field(default_factory=dict)   # fqn -> sentencias
    llm_suggestions: list[dict] = field(default_factory=list)
    deploy_results: list[dict] = field(default_factory=list)
    checks: list = field(default_factory=list)
    synth_manifest: dict | None = None

    def log(self, phase: str, msg: str, **data) -> None:
        ev = {"ts": datetime.now().isoformat(timespec="seconds"), "phase": phase, "msg": msg, **data}
        self.events.append(ev)
        print(f"[{phase}] {msg}")

    def write(self, rel: str, content: str) -> Path:
        p = self.run_dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        self.artifacts[rel] = str(p)
        return p

    def table_ref(self, t: Table) -> str:
        return self.naming.table_ref(t.database, t.name)


class MigrationPipeline:
    def __init__(self, cfg: AgentConfig, run_name: str | None = None):
        run = run_name or datetime.now().strftime("run_%Y%m%d_%H%M%S")
        self.ctx = RunContext(
            cfg=cfg, run_dir=cfg.output_dir / run, matrix=EquivalenceMatrix.load(cfg.matrix_path),
            skills=SkillRegistry(cfg.skills_dir), naming=TargetNaming.from_config(cfg),
            synth_cfg=SynthConfig.from_dict(cfg.get("synthetic", {}) or {}))

    # ================================================================== run
    def run(self, phases: list[str] | None = None) -> RunContext:
        c = self.ctx
        c.run_dir.mkdir(parents=True, exist_ok=True)
        for ph in phases or PHASES:
            t0 = time.time()
            getattr(self, ph.lower())()
            c.log(ph, f"fase completada en {time.time() - t0:.1f}s")
        return c

    # ================================================================== P0
    def p0_probe(self) -> None:
        c = self.ctx
        c.modes = connectivity.resolve_modes(c.cfg)
        c.log("P0_probe", f"origen={c.modes['source']} destino={c.modes['target']}")
        if c.modes["target"] == "online":
            from ..tools.target_online import TrinoTarget
            c.target = TrinoTarget(c.cfg)
        if c.cfg.llm_enabled:
            client = NIMClient(c.cfg)
            ok, detail = client.available()
            c.llm = client if ok else None
            c.log("P0_probe", f"LLM NIM {'disponible' if ok else 'NO disponible (modo deterministico)'}: {detail}")
            if not ok:
                c.findings.append(Finding(Severity.INFO, "llm.unavailable", f"NIM no disponible: {detail}"))

    # ================================================================== P1
    def p1_discover(self) -> None:
        c = self.ctx
        if c.modes.get("source") == "online":
            from ..tools import source_online
            c.inventory, f = source_online.discover(c.cfg)
        else:
            bundle = c.cfg.resolve(c.cfg.get("source.input_bundle"))
            c.inventory, f = source_offline.ingest_bundle(bundle)
        c.findings.extend(f)
        c.translator = Translator(c.matrix, c.naming, c.inventory, bool(c.cfg.get("target.apply_sorted_by", True)))
        c.log("P1_discover", f"inventario: {json.dumps(c.inventory.summary(), ensure_ascii=False)}")

    # ================================================================== P2
    def p2_translate(self) -> None:
        c = self.ctx
        tr = c.translator
        schemas = tr.create_schemas()
        tables_sql, views_sql = [], []
        for t in sorted(c.inventory.tables.values(), key=lambda x: (x.is_view, x.fqn)):
            stmts, f = tr.translate_table(t)
            c.findings.extend(f)
            c.ddl_scripts[t.fqn] = stmts
            (views_sql if t.is_view else tables_sql).append(f"-- {t.fqn} ({t.source_engine})\n" +
                                                            ";\n".join(stmts) + ";")
        c.write("01_ddl/00_create_schemas.sql", _header("Esquemas VAST") + ";\n".join(schemas) + ";\n")
        c.write("01_ddl/01_create_tables.sql", _header("Tablas VAST") + "\n\n".join(tables_sql) + "\n")
        if views_sql:
            c.write("01_ddl/02_create_views.sql", _header("Vistas Trino/VAST") + "\n\n".join(views_sql) + "\n")

        # DML / DQL / sesion (las sentencias DDL de tablas ya se generaron desde el IR)
        by_file: dict[str, list[str]] = {}
        db_by_file: dict[str, str] = {}
        for st in c.inventory.statements:
            m = re.match(r"^\s*USE\s+`?(\w+)`?", st.sql, re.I)
            if m:
                db_by_file[st.source_file] = m.group(1).lower()
            if st.operation in ("CREATE_TABLE", "CREATE_VIEW", "CREATE_DATABASE"):
                st.status = "translated"
                continue
            if st.operation in ("DROP", "ALTER_TABLE", "ALTER_VIEW"):
                self._translate_admin_ddl(st, db_by_file.get(st.source_file, "default"))
            else:
                tr.translate_statement(st, db_by_file.get(st.source_file, "default"))
            c.findings.extend(st.findings)
            if st.kind in (StatementKind.DML, StatementKind.DQL, StatementKind.DDL) or st.status == "manual":
                block = [f"-- {st.id} [{st.operation}] estado={st.status} origen={st.source_file}"]
                if st.status == "manual":
                    block.append("-- REVISION MANUAL REQUERIDA. Original:\n" + _comment(st.sql))
                block += [s + ";" for s in st.translated_sql]
                folder = {StatementKind.DML: "02_dml", StatementKind.DQL: "03_dql",
                          StatementKind.DDL: "01_ddl"}.get(st.kind, "02_dml")
                prefix = "10_alter_" if st.kind == StatementKind.DDL else ""
                key = f"{folder}/{prefix}{Path(st.source_file).stem.replace('.', '_')}.sql"
                by_file.setdefault(key, []).append("\n".join(block))
        for rel, blocks in by_file.items():
            c.write(rel, _header(f"Traduccion de {rel}") + "\n\n".join(blocks) + "\n")
        self._llm_review()
        c.log("P2_translate", f"{len(c.ddl_scripts)} objetos DDL, {len(c.inventory.statements)} sentencias procesadas")

    def _translate_admin_ddl(self, st, db: str) -> None:
        sql = st.sql.strip()
        m = re.match(r"^DROP\s+(TABLE|VIEW)\s+(IF\s+EXISTS\s+)?([`\w.]+)", sql, re.I)
        if m:
            name = m.group(3).replace("`", "")
            tdb, tbl = name.split(".", 1) if "." in name else (db, name)
            st.translated_sql = [f"DROP {m.group(1).upper()} IF EXISTS {self.ctx.naming.table_ref(tdb, tbl)}"]
            st.status = "translated"
            return
        m = re.match(r"^ALTER\s+TABLE\s+([`\w.]+)\s+ADD\s+COLUMNS\s*\((.*)\)\s*$", sql, re.I | re.S)
        if m:
            name = m.group(1).replace("`", "")
            tdb, tbl = name.split(".", 1) if "." in name else (db, name)
            ref = self.ctx.naming.table_ref(tdb, tbl)
            out = []
            for coldef in re.split(r",(?![^<(]*[>)])", m.group(2)):
                parts = coldef.strip().split(None, 1)
                if len(parts) == 2:
                    tm = self.ctx.matrix.map_type(re.sub(r"\s+COMMENT\s+'.*'", "", parts[1]).strip(), name)
                    st.findings.extend(tm.findings)
                    out.append(f"ALTER TABLE {ref} ADD COLUMN {parts[0].strip('`')} {tm.target or 'VARCHAR'}")
            st.translated_sql, st.status = out, "translated"
            return
        if re.search(r"\bCHANGE\s+(COLUMN\s+)?\w+\s+\w+\s+\w+", sql, re.I) or re.search(r"\bREPLACE\s+COLUMNS\b", sql, re.I):
            st.findings.append(self.ctx.matrix.rule_finding("ddl", "alter_change_type", statement_id=st.id))
            st.status = "manual"
            return
        m = re.match(r"^ALTER\s+TABLE\s+([`\w.]+)\s+RENAME\s+TO\s+([`\w.]+)", sql, re.I)
        if m:
            src, dst = (x.replace("`", "") for x in m.groups())
            sdb, stb = src.split(".", 1) if "." in src else (db, src)
            ddb, dtb = dst.split(".", 1) if "." in dst else (sdb, dst)
            st.translated_sql = [f"ALTER TABLE {self.ctx.naming.table_ref(sdb, stb)} RENAME TO "
                                 f"{self.ctx.naming.table_ref(ddb, dtb)}"]
            st.status = "translated"
            return
        if re.search(r"\b(ADD|DROP)\s+PARTITION\b|\bSET\s+(TBLPROPERTIES|SERDEPROPERTIES|LOCATION|FILEFORMAT)\b",
                     sql, re.I):
            st.status = "dropped"
            st.findings.append(Finding(Severity.INFO, "ddl.alter_storage",
                                       "ALTER de particiones/propiedades de almacenamiento: no aplica en VAST",
                                       statement_id=st.id))
            return
        self.ctx.translator.translate_statement(st, db)

    # ---------------------------------------------------------------- LLM review
    def _llm_review(self) -> None:
        """Para sentencias que quedaron 'manual', pide al LLM una propuesta (nunca se aplica sola)."""
        c = self.ctx
        manual = [st for st in c.inventory.statements if st.status == "manual"]
        if not manual:
            return
        if c.llm is None:
            c.log("P2_translate", f"{len(manual)} sentencias requieren revision manual (LLM no disponible)")
            return
        skill = c.skills.get("vast-sql-translation")
        system = ("Eres un experto en migracion de Cloudera (Hive/Impala) a VAST Database consultado via Trino. "
                  "Responde SOLO con JSON: {\"sql\": [\"sentencia Trino\", ...], \"explanation\": \"...\", "
                  "\"confidence\": 0-1}. No inventes funciones; si no hay equivalente devuelve sql vacio.\n\n"
                  + (skill.body if skill else ""))
        from .tools_registry import _validate_syntax
        for st in manual[: int(c.cfg.get("llm.max_review_items", 25))]:
            user = (f"Sentencia origen ({st.operation}):\n{st.sql}\n\nHallazgos:\n" +
                    "\n".join(f"- [{f.severity.value}] {f.rule}: {f.message}" for f in st.findings) +
                    f"\n\nConvencion de nombres destino: {c.naming.table_ref('<db>', '<tabla>')}")
            try:
                out = c.llm.complete_json(system, user)
            except Exception as e:  # noqa: BLE001
                c.log("P2_translate", f"LLM fallo en {st.id}: {e}")
                continue
            sqls = [s for s in out.get("sql", []) if isinstance(s, str) and s.strip()]
            valid = all(_validate_syntax(s)["valid"] for s in sqls) if sqls else False
            c.llm_suggestions.append({"statement": st.id, "operation": st.operation, "sql": sqls,
                                      "syntax_valid": valid, "explanation": out.get("explanation"),
                                      "confidence": out.get("confidence")})
        lines = [_header("Propuestas del LLM (NIM) - REQUIEREN APROBACION HUMANA")]
        for s in c.llm_suggestions:
            lines.append(f"-- {s['statement']} [{s['operation']}] sintaxis_valida={s['syntax_valid']} "
                         f"confianza={s['confidence']}\n-- {s['explanation']}")
            lines += [x.rstrip(';') + ";" for x in s["sql"]] or ["-- (sin propuesta)"]
        c.write("review/llm_suggestions.sql", "\n".join(lines) + "\n")

    # ================================================================== P3
    def p3_security(self) -> None:
        c = self.ctx
        if not c.inventory.policies:
            c.log("P3_security", "sin politicas Ranger en la entrada")
            return
        plan, rules, f = ranger.translate_policies(c.inventory, c.matrix, c.naming)
        c.findings.extend(f)
        c.write("04_security/vast_access_plan.json", json.dumps(plan, indent=2, ensure_ascii=False))
        c.write("04_security/trino_rules.json", json.dumps(rules, indent=2, ensure_ascii=False))
        c.write("04_security/vast_catalog_security.properties",
                "# Agregar al catalogo Trino de VAST (vast.properties) para aplicar reglas fila/columna de VAST\n" +
                "\n".join(f"{k}={v}" for k, v in plan["catalog_flags"].items()) + "\n")
        c.log("P3_security", f"{len(c.inventory.policies)} politicas -> {len(plan['identity_policies'])} identity, "
                             f"{len(plan['column_rules'])} reglas de columna, {len(plan['row_filters'])} filtros")

    # ================================================================== P4
    def p4_synthetic(self) -> None:
        c = self.ctx
        self._llm_semantics()
        gen = SyntheticGenerator(c.inventory, c.synth_cfg)
        gen.generate()
        formats = c.cfg.get("synthetic.formats", ["parquet", "sql"])
        c.synth_manifest = write_outputs(gen, c.run_dir / "05_synthetic", formats, c.table_ref,
                                         int(c.cfg.get("target.insert_batch_rows", 500)))
        order = [t.fqn for t in gen.order()]
        load = [_header("Carga de datos sinteticos en VAST (orden: catalogos -> hechos -> vistas materializadas)")]
        for fqn in order:
            t = c.inventory.get_table(fqn)
            if t.is_view:
                continue
            load.append(f"-- {fqn}: ver 05_synthetic/{t.database}/{t.name}/insert.sql")
        c.write("05_synthetic/LOAD_ORDER.sql", "\n".join(load) + "\n")
        c.log("P4_synthetic", f"{sum(len(v) for v in gen.data.values())} filas en {len(gen.data)} tablas; "
                              f"{len(gen.corruptions)} registros corruptos documentados")

    def _llm_semantics(self) -> None:
        """El LLM refina la semantica de columnas ambiguas (solo sugiere valores del catalogo cerrado)."""
        c = self.ctx
        if c.llm is None:
            return
        from ..tools.synth import SEMANTIC_RULES, infer_semantic
        allowed = sorted({s for _, s in SEMANTIC_RULES} | {"date", "timestamp", "number", "boolean", "category"})
        for t in c.inventory.tables.values():
            amb = [col for col in t.columns if infer_semantic(col) == "category" and col.target_type]
            if not amb:
                continue
            user = (f"Tabla {t.fqn}. Para cada columna elige la semantica mas probable de esta lista: {allowed}.\n"
                    + "\n".join(f"- {col.name} ({col.target_type})" for col in amb) +
                    '\nResponde JSON {"columns": {"<col>": "<semantica>"}}')
            try:
                out = c.llm.complete_json("Clasificas columnas para generar datos sinteticos realistas.", user)
            except Exception:  # noqa: BLE001
                continue
            for name, sem in (out.get("columns") or {}).items():
                col = t.column(name)
                if col is not None and sem in allowed:
                    col.semantic = sem

    # ================================================================== P5
    def p5_validate(self) -> None:
        c = self.ctx
        expected = (c.synth_manifest or {}).get("expected", {})
        c.checks = validate.build_checks(c.inventory, expected, c.table_ref)
        c.write("06_validation/checks.sql", validate.render_checks_sql(c.checks))
        c.write("06_validation/expected_results.json",
                json.dumps({ch.id: ch.to_dict() for ch in c.checks}, indent=2, ensure_ascii=False, default=str))
        if c.target is None:
            c.log("P5_validate", f"destino offline: {len(c.checks)} validaciones listas para ejecucion manual")
            return
        self._deploy_online()
        for ch in c.checks:
            r = c.target.execute(ch.sql)
            if not r.ok:
                ch.status, ch.actual = "error", r.error
            else:
                ch.actual = r.rows[0][0] if r.rows else None
                ch.status = "pass" if validate.compare(ch.expected, ch.actual) else "fail"
        res = {s: sum(1 for x in c.checks if x.status == s) for s in ("pass", "fail", "error")}
        c.write("06_validation/results.json", json.dumps([x.to_dict() for x in c.checks], indent=2, default=str))
        c.log("P5_validate", f"validacion online: {res}")

    def _deploy_online(self) -> None:
        """Ejecuta DDL + carga sintetica en VAST via Trino con ciclo de reparacion por LLM."""
        c = self.ctx
        stmts = c.translator.create_schemas()
        for t in sorted(c.inventory.tables.values(), key=lambda x: x.is_view):
            stmts += c.ddl_scripts.get(t.fqn, [])
        max_rep = int(c.cfg.get("llm.max_repair_attempts", 2))
        for s in stmts:
            r = c.target.execute(s)
            attempt = 0
            while not r.ok and c.llm is not None and attempt < max_rep:
                attempt += 1
                fixed = self._llm_repair(s, r.error)
                if not fixed:
                    break
                c.log("P5_validate", f"reparacion LLM intento {attempt}")
                r = c.target.execute(fixed)
                if r.ok:
                    c.findings.append(Finding(Severity.WARNING, "deploy.llm_repaired",
                                              f"Sentencia reparada por LLM: {fixed[:200]}", resolved_by="llm"))
            c.deploy_results.append({"sql": r.sql, "ok": r.ok, "error": r.error})
        if c.synth_manifest:
            gen_dir = c.run_dir / "05_synthetic"
            for fqn in c.synth_manifest["expected"]:
                t = c.inventory.get_table(fqn)
                p = gen_dir / t.database / t.name / "insert.sql"
                if t.is_view or not p.exists():
                    continue
                from ..tools.sql_parsing import split_statements
                for s in split_statements(p.read_text(encoding="utf-8")):
                    r = c.target.execute(s)
                    c.deploy_results.append({"sql": s[:200], "ok": r.ok, "error": r.error})
        ok = sum(1 for x in c.deploy_results if x["ok"])
        c.log("P5_validate", f"despliegue online: {ok}/{len(c.deploy_results)} sentencias OK")
        c.write("06_validation/deploy_results.json", json.dumps(c.deploy_results, indent=2, ensure_ascii=False))

    def _llm_repair(self, sql: str, error: str | None) -> str | None:
        skill = self.ctx.skills.get("vast-deploy-validate")
        try:
            out = self.ctx.llm.complete_json(
                "Corriges SQL Trino para VAST Database. Responde SOLO JSON {\"sql\": \"...\"}.\n" +
                (skill.body if skill else ""),
                f"La sentencia fallo.\nSQL:\n{sql}\nError:\n{error}\nDevuelve la sentencia corregida.")
            fixed = out.get("sql")
            return fixed if isinstance(fixed, str) and fixed.strip() else None
        except Exception:  # noqa: BLE001
            return None

    # ================================================================== P6
    def p6_package(self) -> None:
        c = self.ctx
        sev = {s.value: 0 for s in Severity}
        for f in c.findings:
            sev[f.severity.value] += 1
        manual = [st for st in c.inventory.statements if st.status == "manual"]
        (c.run_dir / "00_report").mkdir(parents=True, exist_ok=True)
        with open(c.run_dir / "00_report" / "findings.csv", "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=["severity", "rule", "object_name", "statement_id", "message",
                                               "verified", "resolved_by", "suggestion"])
            w.writeheader()
            for f in c.findings:
                w.writerow({k: v for k, v in f.to_dict().items() if k in w.fieldnames})
        report = {
            "run_dir": str(c.run_dir), "modes": c.modes, "matrix_version": c.matrix.version,
            "llm": {"enabled": c.llm is not None, "model": getattr(c.llm, "model", None),
                    "calls": getattr(c.llm, "calls", 0)},
            "inventory": c.inventory.summary(), "findings_by_severity": sev,
            "statements_by_status": _count(st.status for st in c.inventory.statements),
            "manual_statements": [{"id": st.id, "operation": st.operation, "file": st.source_file} for st in manual],
            "llm_suggestions": len(c.llm_suggestions),
            "validation": _count(ch.status for ch in c.checks),
            "artifacts": sorted(c.artifacts), "events": c.events,
        }
        c.write("00_report/report.json", json.dumps(report, indent=2, ensure_ascii=False, default=str))
        c.write("00_report/report.md", _render_report(c, report))
        c.write("review/manual_items.md", _render_manual(c, manual))
        c.write("RUNBOOK.md", _render_runbook(c))
        c.log("P6_package", f"paquete de migracion en {c.run_dir}")


# ---------------------------------------------------------------------- render
def _header(title: str) -> str:
    return (f"-- =====================================================================\n-- {title}\n"
            f"-- Generado por cdp-migration-agent {datetime.now():%Y-%m-%d %H:%M}\n"
            "-- =====================================================================\n")


def _comment(sql: str) -> str:
    return "\n".join("--   " + line for line in sql.splitlines())


def _count(it) -> dict:
    out: dict = {}
    for x in it:
        out[x] = out.get(x, 0) + 1
    return out


def _render_report(c: RunContext, r: dict) -> str:
    inv = r["inventory"]
    lines = [
        "# Reporte de migracion Cloudera CDP -> VAST Database", "",
        f"- **Ejecucion:** `{c.run_dir.name}`",
        f"- **Modo origen:** {c.modes.get('source')}  |  **Modo destino:** {c.modes.get('target')}",
        f"- **LLM (NIM):** {'activo (' + str(r['llm']['model']) + ', ' + str(r['llm']['calls']) + ' llamadas)' if r['llm']['enabled'] else 'no disponible - modo deterministico'}",
        f"- **Matriz de equivalencia:** {r['matrix_version']}", "",
        "## Inventario", "",
        f"| Bases | Tablas | Vistas | Sentencias | Politicas Ranger |", "|---|---|---|---|---|",
        f"| {', '.join(inv['databases'])} | {inv['tables']} | {inv['views']} | "
        f"{', '.join(f'{k}: {v}' for k, v in inv['statements'].items())} | {inv['ranger_policies']} |", "",
        "## Hallazgos", "",
        "| Severidad | Cantidad |", "|---|---|",
        *[f"| {k} | {v} |" for k, v in r["findings_by_severity"].items() if v], "",
        "## Estado de sentencias", "",
        *[f"- **{k}:** {v}" for k, v in r["statements_by_status"].items()], "",
    ]
    if r["manual_statements"]:
        lines += ["## Requieren revision manual", "", "| ID | Operacion | Archivo |", "|---|---|---|",
                  *[f"| {m['id']} | {m['operation']} | {m['file']} |" for m in r["manual_statements"]], ""]
    if r["validation"]:
        lines += ["## Validacion", "", *[f"- **{k}:** {v}" for k, v in r["validation"].items()], ""]
    top = [f for f in c.findings if f.severity in (Severity.MANUAL, Severity.WARNING)]
    seen, uniq = set(), []
    for f in top:
        k = (f.rule, f.message)
        if k not in seen:
            seen.add(k)
            uniq.append(f)
    lines += ["## Advertencias principales (unicas)", "", "| Severidad | Regla | Objeto | Detalle |", "|---|---|---|---|",
              *[f"| {f.severity.value} | `{f.rule}` | {f.object_name or f.statement_id} | {f.message} |" for f in uniq[:60]],
              "", "Detalle completo en `findings.csv`."]
    return "\n".join(lines) + "\n"


def _render_manual(c: RunContext, manual) -> str:
    out = ["# Items de revision manual", ""]
    sugg = {s["statement"]: s for s in c.llm_suggestions}
    for st in manual:
        out += [f"## {st.id} - {st.operation} ({st.source_file})", "", "```sql", st.sql, "```", ""]
        out += [f"- [{f.severity.value}] `{f.rule}`: {f.message}" for f in st.findings]
        if st.id in sugg:
            s = sugg[st.id]
            out += ["", f"**Propuesta LLM** (sintaxis valida: {s['syntax_valid']}, confianza: {s['confidence']}):",
                    "", "```sql", *s["sql"], "```", f"{s['explanation']}"]
        out.append("")
    return "\n".join(out) + "\n"


def _render_runbook(c: RunContext) -> str:
    cat = c.naming.catalog
    return f"""# Runbook de migracion a VAST Database

Paquete generado en modo origen **{c.modes.get('source')}** / destino **{c.modes.get('target')}**.
Ejecutar con el cliente Trino conectado al catalogo `{cat}` (conector VAST).

1. **Prerrequisitos VAST**: bucket `{c.naming.bucket}` creado y usuario con permisos de tabla; catalogo
   Trino `{cat}` configurado (`connector.name=vast`).
2. **Esquemas**: `trino --catalog {cat} -f 01_ddl/00_create_schemas.sql`
3. **Tablas**: `trino --catalog {cat} -f 01_ddl/01_create_tables.sql` (y `02_create_views.sql` si existe).
4. **Revision manual**: resolver `review/manual_items.md` (propuestas del LLM en `review/llm_suggestions.sql`).
5. **Seguridad**: configurar en VMS las identity policies y reglas de `04_security/vast_access_plan.json`;
   agregar `04_security/vast_catalog_security.properties` al catalogo Trino.
6. **Datos sinteticos**: ejecutar `05_synthetic/<db>/<tabla>/insert.sql` en el orden de `05_synthetic/LOAD_ORDER.sql`
   (o importar los Parquet `part-00000.parquet` al bucket).
7. **Validacion**: ejecutar `06_validation/checks.sql` y comparar con `06_validation/expected_results.json`.
8. **DML/DQL**: desplegar `02_dml/*.sql` y `03_dql/*.sql` en los pipelines/consumidores.
"""
