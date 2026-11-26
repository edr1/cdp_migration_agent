"""Cliente LLM para NVIDIA NIM (API compatible con OpenAI /v1/chat/completions).

Sin dependencias externas (urllib) para facilitar el despliegue en la DGX Spark.
Soporta tool calling (formato OpenAI "tools"), salida JSON y degradacion controlada:
si el NIM no responde, el motor sigue en modo deterministico y lo registra en el reporte.
"""
from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Any

from .config import AgentConfig


@dataclass
class LLMResponse:
    content: str
    tool_calls: list[dict] = field(default_factory=list)
    raw: dict = field(default_factory=dict)


class NIMClient:
    def __init__(self, cfg: AgentConfig):
        self.base_url = str(cfg.get("llm.base_url", "http://localhost:8000/v1")).rstrip("/")
        self.model = cfg.get("llm.model")
        self.api_key = cfg.get("llm.api_key", "")
        self.temperature = float(cfg.get("llm.temperature", 0.1))
        self.max_tokens = int(cfg.get("llm.max_tokens", 2048))
        self.timeout = float(cfg.get("llm.timeout_s", 120))
        self.calls = 0

    def available(self) -> tuple[bool, str]:
        try:
            req = urllib.request.Request(f"{self.base_url}/models", headers=self._headers())
            with urllib.request.urlopen(req, timeout=5) as r:  # noqa: S310 (URL de configuracion)
                models = [m.get("id") for m in json.loads(r.read()).get("data", [])]
            if self.model and self.model not in models and models:
                return True, f"modelo '{self.model}' no listado; disponibles: {models}"
            if not self.model and models:
                self.model = models[0]
            return True, f"modelos: {models}"
        except Exception as e:  # noqa: BLE001
            return False, str(e)[:300]

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def chat(self, messages: list[dict], tools: list[dict] | None = None, json_mode: bool = False) -> LLMResponse:
        body: dict[str, Any] = {"model": self.model, "messages": messages, "temperature": self.temperature,
                                "max_tokens": self.max_tokens}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        if json_mode and not tools:
            body["response_format"] = {"type": "json_object"}
        req = urllib.request.Request(f"{self.base_url}/chat/completions", data=json.dumps(body).encode(),
                                     headers=self._headers(), method="POST")
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:  # noqa: S310
                data = json.loads(r.read())
        except urllib.error.HTTPError as e:
            # algunos modelos NIM no aceptan response_format: reintentar sin el
            if json_mode and e.code in (400, 422):
                body.pop("response_format", None)
                req = urllib.request.Request(f"{self.base_url}/chat/completions", data=json.dumps(body).encode(),
                                             headers=self._headers(), method="POST")
                with urllib.request.urlopen(req, timeout=self.timeout) as r:  # noqa: S310
                    data = json.loads(r.read())
            else:
                raise
        self.calls += 1
        msg = data["choices"][0]["message"]
        return LLMResponse(content=msg.get("content") or "", tool_calls=msg.get("tool_calls") or [], raw=data)

    def complete_json(self, system: str, user: str) -> dict:
        resp = self.chat([{"role": "system", "content": system}, {"role": "user", "content": user}], json_mode=True)
        return extract_json(resp.content)


def extract_json(text: str) -> dict:
    """Extrae el primer objeto JSON de la respuesta (tolera ```json ...``` y texto alrededor)."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)  # modelos de razonamiento
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidate = m.group(1) if m else text[text.find("{"): text.rfind("}") + 1]
    return json.loads(candidate) if candidate else {}
