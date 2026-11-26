"""FASE 0: deteccion de conectividad para decidir modo online/offline de origen y destino."""
from __future__ import annotations

import socket
from dataclasses import asdict, dataclass

from ..core.config import AgentConfig


@dataclass
class Probe:
    name: str
    host: str
    port: int
    reachable: bool
    detail: str = ""


def tcp_probe(name: str, host: str, port: int, timeout: float = 3.0) -> Probe:
    if not host:
        return Probe(name, host, port, False, "host no configurado")
    try:
        with socket.create_connection((host, int(port)), timeout=timeout):
            return Probe(name, host, int(port), True, "puerto abierto")
    except OSError as e:
        return Probe(name, host, int(port), False, str(e))


def probe_trino(cfg: AgentConfig) -> Probe:
    t = cfg.get("target.trino", {})
    p = tcp_probe("trino", t.get("host"), int(t.get("port", 8080)))
    if not p.reachable:
        return p
    try:
        import trino  # noqa: F401
        from .target_online import TrinoTarget
        tgt = TrinoTarget(cfg)
        catalogs = [r[0] for r in tgt.query("SHOW CATALOGS")]
        cat = t.get("catalog", "vast")
        p.reachable = cat in catalogs
        p.detail = f"catalogos={catalogs}" + ("" if p.reachable else f" (falta catalogo '{cat}')")
    except Exception as e:  # noqa: BLE001
        p.reachable, p.detail = False, f"puerto abierto pero consulta fallo: {e}"[:300]
    return p


def probe_hive(cfg: AgentConfig) -> Probe:
    h = cfg.get("source.hive", {})
    p = tcp_probe("hiveserver2", h.get("host"), int(h.get("port", 10000)))
    if not p.reachable:
        return p
    try:
        from .source_online import HiveSource
        HiveSource(cfg).query("SHOW DATABASES")
        p.detail = "HiveServer2 responde"
    except Exception as e:  # noqa: BLE001
        p.reachable, p.detail = False, f"puerto abierto pero consulta fallo: {e}"[:300]
    return p


def resolve_modes(cfg: AgentConfig) -> dict:
    """Devuelve {'source': 'online'|'offline', 'target': ..., 'probes': [...]}."""
    probes: list[Probe] = []
    modes = {}
    for side, probe_fn in (("source", probe_hive), ("target", probe_trino)):
        wanted = str(cfg.get(f"{side}.mode", "auto")).lower()
        if wanted == "offline":
            modes[side] = "offline"
            continue
        pr = probe_fn(cfg)
        probes.append(pr)
        if wanted == "online" and not pr.reachable:
            raise ConnectionError(f"{side} configurado como online pero no es accesible: {pr.detail}")
        modes[side] = "online" if pr.reachable else "offline"
    modes["probes"] = [asdict(p) for p in probes]
    return modes
