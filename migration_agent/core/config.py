"""Carga de configuracion YAML con expansion de variables ${VAR:-default}."""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_ENV = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)(?::-([^}]*))?\}")
ROOT = Path(__file__).resolve().parents[2]


def _expand(value: Any) -> Any:
    if isinstance(value, str):
        return _ENV.sub(lambda m: os.environ.get(m.group(1), m.group(2) or ""), value)
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_expand(v) for v in value]
    return value


def _as_bool(v: Any) -> bool:
    return str(v).strip().lower() in {"1", "true", "yes", "on"}


@dataclass
class AgentConfig:
    raw: dict[str, Any] = field(default_factory=dict)
    path: Path | None = None

    # ---- accesos rapidos ---------------------------------------------------
    def get(self, dotted: str, default: Any = None) -> Any:
        node: Any = self.raw
        for part in dotted.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    @property
    def llm_enabled(self) -> bool:
        return _as_bool(self.get("llm.enabled", False))

    @property
    def output_dir(self) -> Path:
        return self.resolve(self.get("output_dir", "out"))

    @property
    def matrix_path(self) -> Path:
        return self.resolve(self.get("matrix", "config/equivalence_matrix.yaml"))

    @property
    def skills_dir(self) -> Path:
        return self.resolve(self.get("skills_dir", "skills"))

    def resolve(self, p: str | Path) -> Path:
        p = Path(p)
        return p if p.is_absolute() else (ROOT / p)


def load_config(path: str | Path | None = None, overrides: dict[str, Any] | None = None) -> AgentConfig:
    path = Path(path) if path else ROOT / "config" / "agent.yaml"
    raw = _expand(yaml.safe_load(path.read_text(encoding="utf-8")) or {})
    for dotted, value in (overrides or {}).items():
        if value is None:
            continue
        node = raw
        parts = dotted.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    return AgentConfig(raw=raw, path=path)
