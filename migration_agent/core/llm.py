"""Clientes LLM del motor agentico.

Proveedores (llm.provider):
  * ollama  -> API nativa de Ollama (/api/chat) en un servidor remoto, p. ej. la DGX Spark con
               "nemotron-3.5-lightning:30b-a3b". Permite fijar num_ctx, think y keep_alive por peticion,
               cosa que el endpoint compatible OpenAI de Ollama no expone.
  * openai  -> cualquier API compatible OpenAI /v1/chat/completions (NVIDIA NIM, vLLM, Ollama /v1).

Ambos exponen la misma interfaz (available, chat, complete_json) y devuelven tool_calls en formato
OpenAI, de modo que el pipeline y el modo agente no dependen del proveedor.
Sin dependencias externas (urllib). Si el LLM no responde, el motor sigue en modo deterministico.
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
    tool_calls: list[dict] = field(default_factory=list)   # formato OpenAI
    thinking: str = ""
    raw: dict = field(default_factory=dict)


class BaseLLM:
    provider = "base"

    def __init__(self, cfg: AgentConfig):
        self.base_url = str(cfg.get("llm.base_url", "")).rstrip("/")
        self.model = cfg.get("llm.model")
        self.api_key = cfg.get("llm.api_key", "")
        self.temperature = float(cfg.get("llm.temperature", 0.1))
        self.max_tokens = int(cfg.get("llm.max_tokens", 2048))
        self.timeout = float(cfg.get("llm.timeout_s", 180))
        self.calls = 0

    def _headers(self) -> dict:
        h = {"Content-Type": "application/json"}
        if self.api_key:
            h["Authorization"] = f"Bearer {self.api_key}"
        return h

    def _post(self, path: str, body: dict) -> dict:
        req = urllib.request.Request(f"{self.base_url}{path}", data=json.dumps(body).encode(),
                                     headers=self._headers(), method="POST")
        with urllib.request.urlopen(req, timeout=self.timeout) as r:  # noqa: S310 (URL de configuracion)
            return json.loads(r.read())

    def _get(self, path: str, timeout: float = 5) -> dict:
        req = urllib.request.Request(f"{self.base_url}{path}", headers=self._headers())
        with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
            return json.loads(r.read())

    def available(self) -> tuple[bool, str]:
        raise NotImplementedError

    def chat(self, messages: list[dict], tools: list[dict] | None = None, json_mode: bool = False) -> LLMResponse:
        raise NotImplementedError

    def complete_json(self, system: str, user: str) -> dict:
        resp = self.chat([{"role": "system", "content": system}, {"role": "user", "content": user}], json_mode=True)
        return extract_json(resp.content)


# =========================================================================== Ollama
class OllamaClient(BaseLLM):
    """Ollama remoto (API nativa). Ejemplo: base_url=http://dgx-spark:11434,
    model=nemotron-3.5-lightning:30b-a3b."""
    provider = "ollama"

    def __init__(self, cfg: AgentConfig):
        super().__init__(cfg)
        self.base_url = re.sub(r"/v1/?$", "", self.base_url or "http://localhost:11434")
        self.num_ctx = int(cfg.get("llm.num_ctx", 32768))
        self.keep_alive = str(cfg.get("llm.keep_alive", "30m"))
        think = str(cfg.get("llm.think", "false")).strip().lower()
        # true | false | low | medium | high  (Ollama acepta bool o nivel segun el modelo)
        self.think: bool | str | None = {"true": True, "false": False, "": None}.get(think, think)

    def available(self) -> tuple[bool, str]:
        try:
            names = [m.get("name") or m.get("model") for m in self._get("/api/tags").get("models", [])]
        except Exception as e:  # noqa: BLE001
            return False, f"Ollama no responde en {self.base_url}: {e}"[:300]
        if self.model not in names and f"{self.model}:latest" not in names:
            return False, (f"modelo '{self.model}' no esta descargado en {self.base_url} "
                           f"(ejecuta en la DGX: ollama pull {self.model}); disponibles: {names}")
        return True, f"Ollama {self.base_url} modelo {self.model} (num_ctx={self.num_ctx}, think={self.think})"

    def chat(self, messages: list[dict], tools: list[dict] | None = None, json_mode: bool = False) -> LLMResponse:
        body: dict[str, Any] = {
            "model": self.model, "messages": _to_ollama_messages(messages), "stream": False,
            "keep_alive": self.keep_alive,
            "options": {"temperature": self.temperature, "num_ctx": self.num_ctx, "num_predict": self.max_tokens},
        }
        if tools:
            body["tools"] = tools
        if json_mode and not tools:
            body["format"] = "json"
        if self.think is not None:
            body["think"] = self.think
        try:
            data = self._post("/api/chat", body)
        except urllib.error.HTTPError as e:
            detail = e.read().decode(errors="ignore")
            # modelos sin soporte de "think" o de "format": reintentar sin el parametro
            if "think" in detail.lower() and "think" in body:
                body.pop("think")
                data = self._post("/api/chat", body)
            else:
                raise RuntimeError(f"Ollama HTTP {e.code}: {detail[:300]}") from e
        self.calls += 1
        msg = data.get("message", {})
        calls = []
        for i, tc in enumerate(msg.get("tool_calls") or []):
            fn = tc.get("function", {})
            args = fn.get("arguments", {})
            calls.append({"id": tc.get("id") or f"call_{self.calls}_{i}", "type": "function",
                          "function": {"name": fn.get("name", ""),
                                       "arguments": args if isinstance(args, str) else json.dumps(args)}})
        return LLMResponse(content=msg.get("content") or "", tool_calls=calls,
                           thinking=msg.get("thinking") or "", raw=data)


def _to_ollama_messages(messages: list[dict]) -> list[dict]:
    """Convierte el historial formato OpenAI (usado por el modo agente) al formato nativo de Ollama."""
    out, names = [], {}
    for m in messages:
        if m.get("role") == "assistant" and m.get("tool_calls"):
            calls = []
            for tc in m["tool_calls"]:
                fn = tc.get("function", {})
                args = fn.get("arguments") or "{}"
                names[tc.get("id", "")] = fn.get("name", "")
                calls.append({"function": {"name": fn.get("name", ""),
                                           "arguments": json.loads(args) if isinstance(args, str) else args}})
            out.append({"role": "assistant", "content": m.get("content") or "", "tool_calls": calls})
        elif m.get("role") == "tool":
            out.append({"role": "tool", "content": m.get("content", ""),
                        "tool_name": names.get(m.get("tool_call_id", ""), m.get("name", ""))})
        else:
            out.append({"role": m["role"], "content": m.get("content", "")})
    return out


# =========================================================================== OpenAI-compatible
class OpenAICompatClient(BaseLLM):
    """NVIDIA NIM, vLLM u Ollama /v1 (API OpenAI /v1/chat/completions)."""
    provider = "openai"

    def available(self) -> tuple[bool, str]:
        try:
            models = [m.get("id") for m in self._get("/models").get("data", [])]
        except Exception as e:  # noqa: BLE001
            return False, str(e)[:300]
        if not self.model and models:
            self.model = models[0]
        if self.model and models and self.model not in models:
            return True, f"modelo '{self.model}' no listado; disponibles: {models}"
        return True, f"modelos: {models}"

    def chat(self, messages: list[dict], tools: list[dict] | None = None, json_mode: bool = False) -> LLMResponse:
        body: dict[str, Any] = {"model": self.model, "messages": messages, "temperature": self.temperature,
                                "max_tokens": self.max_tokens}
        if tools:
            body["tools"] = tools
            body["tool_choice"] = "auto"
        if json_mode and not tools:
            body["response_format"] = {"type": "json_object"}
        try:
            data = self._post("/chat/completions", body)
        except urllib.error.HTTPError as e:
            if json_mode and e.code in (400, 422):   # algunos servidores no aceptan response_format
                body.pop("response_format", None)
                data = self._post("/chat/completions", body)
            else:
                raise
        self.calls += 1
        msg = data["choices"][0]["message"]
        return LLMResponse(content=msg.get("content") or "", tool_calls=msg.get("tool_calls") or [],
                           thinking=msg.get("reasoning_content") or "", raw=data)


# Alias de compatibilidad con la version anterior
NIMClient = OpenAICompatClient


def create_llm(cfg: AgentConfig) -> BaseLLM:
    provider = str(cfg.get("llm.provider", "ollama")).strip().lower()
    if provider == "ollama":
        return OllamaClient(cfg)
    if provider in ("openai", "nim", "vllm"):
        return OpenAICompatClient(cfg)
    raise ValueError(f"llm.provider desconocido: {provider} (usa ollama | openai)")


def extract_json(text: str) -> dict:
    """Extrae el primer objeto JSON (tolera ```json```, texto alrededor y bloques <think> de modelos de razonamiento)."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S)
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    candidate = m.group(1) if m else text[text.find("{"): text.rfind("}") + 1]
    return json.loads(candidate) if candidate else {}
