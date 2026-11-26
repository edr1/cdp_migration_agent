"""CLI del motor agentico de migracion Cloudera CDP -> VAST Database.

Ejemplos:
    python -m migration_agent probe
    python -m migration_agent run --source-mode offline --target-mode offline
    python -m migration_agent run --bundle examples/input_bundle --no-llm
    python -m migration_agent translate --sql "INSERT OVERWRITE TABLE db.t SELECT nvl(a,'x') FROM db.s"
    python -m migration_agent ask "Que columnas de db_consulting requieren revision?"
"""
from __future__ import annotations

import argparse
import json
import sys

from .agent.pipeline import PHASES, MigrationPipeline
from .core.config import load_config
from .tools import connectivity


def _cfg(args):
    return load_config(args.config, {
        "source.mode": getattr(args, "source_mode", None),
        "target.mode": getattr(args, "target_mode", None),
        "source.input_bundle": getattr(args, "bundle", None),
        "target.naming": getattr(args, "naming", None),
        "target.trino.catalog": getattr(args, "catalog", None),
        "target.bucket": getattr(args, "bucket", None),
        "synthetic.rows_default": getattr(args, "rows", None),
        "synthetic.corruption_rate": getattr(args, "corruption_rate", None),
        "output_dir": getattr(args, "out", None),
        "llm.enabled": False if getattr(args, "no_llm", False) else None,
    })


def main(argv=None) -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(prog="migration_agent", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--config", default=None, help="ruta a agent.yaml (default config/agent.yaml)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--source-mode", choices=["auto", "online", "offline"])
        p.add_argument("--target-mode", choices=["auto", "online", "offline"])
        p.add_argument("--bundle", help="paquete de entrada offline (DDL/DML/DQL/Ranger)")
        p.add_argument("--naming", choices=["vast", "plain"])
        p.add_argument("--catalog")
        p.add_argument("--bucket")
        p.add_argument("--no-llm", action="store_true", help="modo 100%% deterministico (sin LLM)")
        p.add_argument("--out")

    sub.add_parser("probe", help="detecta conectividad a CDP, VAST/Trino y al LLM (Ollama/NIM)")
    p_run = sub.add_parser("run", help="ejecuta el pipeline completo (o fases con --phases)")
    common(p_run)
    p_run.add_argument("--phases", help=f"subset separado por comas de {','.join(PHASES)}")
    p_run.add_argument("--rows", type=int, help="filas sinteticas por tabla")
    p_run.add_argument("--corruption-rate", type=float)
    p_run.add_argument("--run-name")
    p_tr = sub.add_parser("translate", help="traduce una sentencia Hive/Impala a VAST")
    common(p_tr)
    p_tr.add_argument("--sql", required=True)
    p_tr.add_argument("--db", default="default")
    p_ask = sub.add_parser("ask", help="modo agente interactivo (requiere LLM)")
    common(p_ask)
    p_ask.add_argument("question")

    args = ap.parse_args(argv)
    if args.cmd == "probe":
        cfg = load_config(args.config)
        modes = connectivity.resolve_modes(cfg)
        from .core.llm import create_llm
        llm = create_llm(cfg)
        ok, detail = llm.available()
        modes["llm"] = {"provider": llm.provider, "model": llm.model, "available": ok, "detail": detail}
        print(json.dumps(modes, indent=2, ensure_ascii=False))
        return 0

    cfg = _cfg(args)
    pipe = MigrationPipeline(cfg, getattr(args, "run_name", None))
    if args.cmd == "run":
        phases = args.phases.split(",") if args.phases else None
        if phases and "P0_probe" not in phases:
            phases = ["P0_probe"] + phases
        if phases and "P1_discover" not in phases:
            phases.insert(1, "P1_discover")
        ctx = pipe.run(phases)
        print(f"\nPaquete: {ctx.run_dir}\nReporte: {ctx.run_dir / '00_report' / 'report.md'}")
        return 0
    pipe.run(["P0_probe", "P1_discover"])
    if args.cmd == "translate":
        from .agent.tools_registry import _translate
        print(json.dumps(_translate(pipe.ctx, args.sql, args.db), indent=2, ensure_ascii=False))
        return 0
    if args.cmd == "ask":
        from .agent.react import run_agent
        print(run_agent(pipe, args.question))
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
