"""Cargador de skills (SKILL.md con frontmatter YAML, divulgacion progresiva).

Cada skill es una carpeta skills/<NN-nombre>/ con:
  SKILL.md            -> frontmatter (name, description, phase, tools, ...) + instrucciones
  references/*.md     -> detalle cargado solo cuando la skill lo necesita
El orquestador inyecta el SKILL.md de la fase activa en el prompt del LLM; las referencias
se cargan bajo demanda con la tool read_skill_reference.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass
class Skill:
    name: str
    description: str
    phase: str
    path: Path
    body: str
    meta: dict = field(default_factory=dict)

    def references(self) -> list[str]:
        ref = self.path.parent / "references"
        return sorted(p.name for p in ref.glob("*.md")) if ref.exists() else []

    def reference(self, name: str) -> str:
        p = (self.path.parent / "references" / name).resolve()
        if self.path.parent.resolve() not in p.parents:
            raise ValueError("referencia fuera de la skill")
        return p.read_text(encoding="utf-8")


class SkillRegistry:
    def __init__(self, root: Path):
        self.root = root
        self.skills: dict[str, Skill] = {}
        for md in sorted(root.glob("*/SKILL.md")):
            text = md.read_text(encoding="utf-8")
            meta, body = {}, text
            if text.startswith("---"):
                _, fm, body = text.split("---", 2)
                meta = yaml.safe_load(fm) or {}
            sk = Skill(name=meta.get("name", md.parent.name), description=meta.get("description", ""),
                       phase=str(meta.get("phase", "")), path=md, body=body.strip(), meta=meta)
            self.skills[sk.name] = sk

    def get(self, name: str) -> Skill | None:
        return self.skills.get(name)

    def for_phase(self, phase: str) -> list[Skill]:
        return [s for s in self.skills.values() if s.phase == phase]

    def index(self) -> str:
        return "\n".join(f"- {s.name} (fase {s.phase}): {s.description}" for s in self.skills.values())
