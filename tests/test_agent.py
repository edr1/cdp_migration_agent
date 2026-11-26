"""Pruebas del motor (sin CDP ni VAST): traduccion, Ranger, sinteticos, pipeline offline y
nodos LLM contra un NIM simulado (servidor HTTP compatible OpenAI).

    python -m unittest discover -s tests -v
"""
from __future__ import annotations

import json
import shutil
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path

from migration_agent.agent.pipeline import MigrationPipeline
from migration_agent.core.config import load_config
from migration_agent.core.ir import Severity
from migration_agent.core.matrix import EquivalenceMatrix
from migration_agent.tools.sql_parsing import classify, split_statements, table_from_ddl

ROOT = Path(__file__).resolve().parents[1]


class FakeNIM(BaseHTTPRequestHandler):
    """Simula /v1/models y /v1/chat/completions (incluye tool calling)."""
    def log_message(self, *a):  # silencio
        pass

    def _send(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._send({"data": [{"id": "fake/model"}]})

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        msgs = req["messages"]
        if req.get("tools"):
            if not any(m["role"] == "tool" for m in msgs):
                msg = {"role": "assistant", "content": "", "tool_calls": [{
                    "id": "c1", "type": "function", "function": {
                        "name": "translate_sql", "arguments": json.dumps({"sql": "SELECT nvl(a,'x') FROM db.t"})}}]}
            else:
                msg = {"role": "assistant", "content": "Traduccion lista: COALESCE"}
        elif "Clasificas columnas" in msgs[0]["content"]:
            msg = {"role": "assistant", "content": json.dumps({"columns": {}})}
        else:
            msg = {"role": "assistant", "content": "```json\n" + json.dumps(
                {"sql": ["SELECT 1"], "explanation": "propuesta de prueba", "confidence": 0.5}) + "\n```"}
        self._send({"choices": [{"message": msg}]})


class FakeOllama(BaseHTTPRequestHandler):
    """Simula la API nativa de Ollama: /api/tags y /api/chat (tool calls con argumentos como objeto)."""
    requests: list = []

    def log_message(self, *a):
        pass

    def _send(self, obj):
        body = json.dumps(obj).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._send({"models": [{"name": "nemotron-3.5-lightning:30b-a3b"}]})

    def do_POST(self):
        req = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        FakeOllama.requests.append(req)
        msgs = req["messages"]
        if req.get("tools"):
            if not any(m["role"] == "tool" for m in msgs):
                msg = {"role": "assistant", "content": "", "tool_calls": [
                    {"function": {"name": "translate_sql", "arguments": {"sql": "SELECT nvl(a,'x') FROM db.t"}}}]}
            else:
                msg = {"role": "assistant", "content": "Traduccion lista: COALESCE"}
        elif "Clasificas columnas" in msgs[0]["content"]:
            msg = {"role": "assistant", "content": json.dumps({"columns": {}})}
        else:
            msg = {"role": "assistant", "thinking": "analizando...",
                   "content": json.dumps({"sql": ["SELECT 1"], "explanation": "propuesta", "confidence": 0.6})}
        self._send({"model": req["model"], "message": msg, "done": True})


class TestTranslation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.m = EquivalenceMatrix.load(ROOT / "config" / "equivalence_matrix.yaml")

    def test_types(self):
        self.assertEqual(self.m.map_type("STRING").target, "VARCHAR")
        self.assertEqual(self.m.map_type("DECIMAL(40,2)").target, "DECIMAL(38, 2)")
        self.assertEqual(self.m.map_type("ARRAY<STRUCT<a:INT,b:STRING>>").target, "ARRAY(ROW(a INTEGER, b VARCHAR))")
        self.assertEqual(self.m.map_type("MAP<STRING,BIGINT>").target, "MAP(VARCHAR, BIGINT)")
        self.assertIsNone(self.m.map_type("UNIONTYPE<INT,STRING>").target)

    def test_split_and_classify(self):
        sql = "#------ Hive\nSET a=1;\n-- c\nINSERT OVERWRITE TABLE t SELECT ';' FROM s;\nSELECT 1"
        parts = split_statements(sql)
        self.assertEqual(len(parts), 3)
        self.assertEqual(classify(parts[1])[1], "INSERT_OVERWRITE")

    def test_impala_sort_by(self):
        t, _ = table_from_ddl("CREATE TABLE d.x (a INT, b STRING) PARTITIONED BY (p INT) SORT BY (a) STORED AS PARQUET")
        self.assertEqual(t.sort_columns, ["a"])
        self.assertEqual(t.partition_columns, ["p"])


class TestPipeline(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _cfg(self, **over):
        base = {"source.mode": "offline", "target.mode": "offline", "output_dir": str(self.tmp),
                "llm.enabled": False, "synthetic.rows_default": 50}
        base.update(over)
        return load_config(ROOT / "config" / "agent.yaml", base)

    def test_offline_end_to_end(self):
        ctx = MigrationPipeline(self._cfg(), "t").run()
        ddl = (ctx.run_dir / "01_ddl" / "01_create_tables.sql").read_text(encoding="utf-8")
        self.assertIn('vast."cdp-migration/db_production".tbl_certificacion_logs', ddl)
        self.assertIn("partitioning = ARRAY['anio', 'mes', 'bucket(nm_computadora, 16)']", ddl)
        dql = (ctx.run_dir / "03_dql" / "01_reportes.sql").read_text(encoding="utf-8")
        self.assertIn("CAST('2026-01-01' AS DATE)", dql)          # conversion implicita explicitada
        self.assertIn("CROSS JOIN UNNEST", dql)
        self.assertNotIn("[SHUFFLE]", (ctx.run_dir / "02_dml" / "02_etl_consulting_impala.sql").read_text(encoding="utf-8"))
        rules = json.loads((ctx.run_dir / "04_security" / "trino_rules.json").read_text(encoding="utf-8"))
        self.assertTrue(any("filter" in r for r in rules["tables"]))
        manifest = json.loads((ctx.run_dir / "05_synthetic" / "manifest.json").read_text(encoding="utf-8"))
        cat = manifest["expected"]["db_production.cat_certificacion_valor_sensor"]
        self.assertEqual(cat["rows"], 6)
        self.assertEqual(cat["columns"]["valor_sensor"]["nulls"], 0)
        self.assertTrue((ctx.run_dir / "06_validation" / "checks.sql").exists())
        self.assertTrue(any(f.severity == Severity.MANUAL for f in ctx.findings))

    def test_plain_naming_for_local_emulation(self):
        ctx = MigrationPipeline(self._cfg(**{"target.naming": "plain", "target.trino.catalog": "iceberg"}), "p").run(
            ["P0_probe", "P1_discover", "P2_translate"])
        ddl = (ctx.run_dir / "01_ddl" / "01_create_tables.sql").read_text(encoding="utf-8")
        self.assertIn("iceberg.db_production.tbl_certificacion_logs", ddl)

    def test_llm_nodes_with_fake_ollama(self):
        """Proveedor por defecto: Ollama nativo con Nemotron 3.5 Lightning."""
        FakeOllama.requests.clear()
        srv = HTTPServer(("127.0.0.1", 0), FakeOllama)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            url = f"http://127.0.0.1:{srv.server_port}"
            pipe = MigrationPipeline(self._cfg(**{"llm.enabled": True, "llm.provider": "ollama", "llm.base_url": url,
                                                  "llm.model": "nemotron-3.5-lightning:30b-a3b"}), "o")
            ctx = pipe.run(["P0_probe", "P1_discover", "P2_translate"])
            self.assertEqual(ctx.llm.provider, "ollama")
            self.assertTrue(ctx.llm_suggestions)
            self.assertTrue(all(s["syntax_valid"] for s in ctx.llm_suggestions))
            first = FakeOllama.requests[0]
            self.assertEqual(first["model"], "nemotron-3.5-lightning:30b-a3b")
            self.assertEqual(first["options"]["num_ctx"], 32768)
            self.assertEqual(first["format"], "json")
            self.assertIs(first["think"], False)
            from migration_agent.agent.react import run_agent
            answer = run_agent(pipe, "traduce SELECT nvl(a,'x') FROM db.t")
            self.assertIn("COALESCE", answer)
            # el historial reenviado a Ollama lleva argumentos como objeto y tool_name en la respuesta de la tool
            last = FakeOllama.requests[-1]["messages"]
            self.assertIsInstance(next(m for m in last if m.get("tool_calls"))["tool_calls"][0]["function"]["arguments"], dict)
            self.assertEqual(next(m for m in last if m["role"] == "tool")["tool_name"], "translate_sql")
        finally:
            srv.shutdown()
            srv.server_close()

    def test_ollama_missing_model_degrades(self):
        FakeOllama.requests.clear()
        srv = HTTPServer(("127.0.0.1", 0), FakeOllama)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            url = f"http://127.0.0.1:{srv.server_port}"
            ctx = MigrationPipeline(self._cfg(**{"llm.enabled": True, "llm.provider": "ollama", "llm.base_url": url,
                                                 "llm.model": "modelo-inexistente:1b"}), "m").run(["P0_probe"])
            self.assertIsNone(ctx.llm)
            self.assertTrue(any(f.rule == "llm.unavailable" and "ollama pull" in f.message for f in ctx.findings))
        finally:
            srv.shutdown()
            srv.server_close()

    def test_llm_nodes_with_fake_openai_compatible(self):
        """Proveedor alternativo: API compatible OpenAI (NVIDIA NIM / vLLM)."""
        srv = HTTPServer(("127.0.0.1", 0), FakeNIM)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        try:
            url = f"http://127.0.0.1:{srv.server_port}/v1"
            pipe = MigrationPipeline(self._cfg(**{"llm.enabled": True, "llm.provider": "openai", "llm.base_url": url,
                                                  "llm.model": "fake/model"}), "l")
            ctx = pipe.run(["P0_probe", "P1_discover", "P2_translate"])
            self.assertIsNotNone(ctx.llm)
            self.assertTrue(ctx.llm_suggestions)
            self.assertTrue(all(s["syntax_valid"] for s in ctx.llm_suggestions))
            from migration_agent.agent.react import run_agent
            answer = run_agent(pipe, "traduce SELECT nvl(a,'x') FROM db.t")
            self.assertIn("COALESCE", answer)
        finally:
            srv.shutdown()
            srv.server_close()


if __name__ == "__main__":
    unittest.main()
