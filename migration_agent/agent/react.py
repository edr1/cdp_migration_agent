"""Modo agente interactivo (ReAct con tool calling; Nemotron via Ollama o NIM).

Permite al usuario pedir en lenguaje natural tareas puntuales ("traduce esta consulta", "que tipos
de db_consulting requieren revision", "genera 5 filas de ejemplo de X") usando las mismas tools
deterministicas del pipeline. Las tools con efectos sobre VAST solo se exponen si el destino esta
online y son de solo lectura.
"""
from __future__ import annotations

import json

from .pipeline import MigrationPipeline
from .tools_registry import ToolRegistry

SYSTEM = """Eres el agente de migracion Cloudera CDP (Hive/Impala) -> VAST Database (via Trino).
Reglas:
- Usa SIEMPRE las tools para obtener hechos (inventario, matriz, traduccion); no inventes sintaxis VAST.
- Para traducir SQL llama translate_sql y luego validate_trino_syntax.
- Si una regla es severity=manual, explicalo y propone alternativas citando la matriz (lookup_matrix).
- Responde en espanol, conciso, con el SQL final en bloques ```sql```.
Skills disponibles (lee la relevante con read_skill antes de tareas complejas):
{skills}"""


def run_agent(pipeline: MigrationPipeline, question: str, max_steps: int = 8) -> str:
    c = pipeline.ctx
    if c.llm is None:
        raise RuntimeError("El modo agente requiere el LLM disponible: revisa llm.base_url / LLM_BASE_URL y llm.model / LLM_MODEL")
    reg = ToolRegistry(c)
    tools = reg.specs(allow_side_effects=c.target is not None)
    messages = [{"role": "system", "content": SYSTEM.format(skills=c.skills.index())},
                {"role": "user", "content": question}]
    for _ in range(max_steps):
        resp = c.llm.chat(messages, tools=tools)
        if not resp.tool_calls:
            return resp.content
        messages.append({"role": "assistant", "content": resp.content or "", "tool_calls": resp.tool_calls})
        for call in resp.tool_calls:
            fn = call.get("function", {})
            result = reg.call(fn.get("name", ""), fn.get("arguments") or "{}")
            c.log("agent", f"tool {fn.get('name')}({(fn.get('arguments') or '')[:120]})")
            messages.append({"role": "tool", "tool_call_id": call.get("id", ""), "content": result})
    return "Se alcanzo el maximo de pasos sin respuesta final. Ultimo estado:\n" + json.dumps(messages[-1])[:2000]
